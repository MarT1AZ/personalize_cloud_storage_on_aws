from datetime import datetime, timezone
import base64
import logging
from pathlib import PurePosixPath
import secrets

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, EndpointConnectionError, NoCredentialsError, NoRegionError, ParamValidationError, PartialCredentialsError, ReadTimeoutError
from fastapi import HTTPException
import jwt

from app.aws_error_handling import build_error_result
from app.auth_config import auth_settings
from app.config import settings
from app.resource_guard import ResourceGuard


logger = logging.getLogger(__name__)


class ObjectStorageService:
    PARENT_FOLDER_INDEX = "parent_folder_id_index"
    ROOT_PARENT_FOLDER_ID = "__root__"
    LOG_STATUS_RUNNING = "running"
    LOG_STATUS_FAILED = "failed"
    LOG_STATUS_DONE = "done"
    LOG_STATUS_REPAIR_REQUIRED = "repair_required"
    LOG_OPERATION_DELETE = "hard_delete"
    LOG_OPERATION_PURGE = "purge"
    LOG_OPERATION_MOVE = "move"
    LOG_ATTEMPT_INITIAL = "initial"
    LOG_ATTEMPT_RESUME = "resume"
    MOVE_MODE_MERGE = "merge"
    MOVE_MODE_AVOID_CONFLICT = "avoid_conflict"
    STATUS_ACTIVE = "active"
    STATUS_DELETED = "deleted"
    STATUS_MOVED = "moved"
    STATUS_PENDING = "pending"
    STATUS_REPLACEMENT_PENDING_DELETE = "replacement_pending_delete"
    UPLOAD_STATE_UPLOADING = "uploading"
    UPLOAD_STATE_PENDING_FINALIZE = "pending_finalize"
    UPLOAD_STATE_FINALIZING = "finalizing"
    UPLOAD_STATE_REPAIR_REQUIRED = "repair_required"
    REPLACEMENT_DELETE_STATUS_PENDING = "pending_delete"
    REPLACEMENT_DELETE_STATUS_DELETED = "deleted"
    REPLACEMENT_DELETE_STATUS_RESTORED = "restored"
    REPLACEMENT_DELETE_STATUS_FAILED = "failed"
    MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
    DIRECT_UPLOAD_EXPIRES_SECONDS = 900

    def __init__(
        self,
        bucket: str,
        user_id: str,
        file_metadata_table: str,
        folder_metadata_table: str,
        deletion_log_table: str,
        replacement_table: str,
        folder_upload_log_table: str,
        move_log_table: str,
        purge_log_table: str,
        operation_batch_limit: int = 100,
        s3_client=None,
        dynamodb_resource=None,
    ):
        self.bucket = bucket
        self.user_id = user_id
        self.s3 = s3_client or boto3.client("s3", region_name=settings.aws_region)
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb", region_name=settings.aws_region)
        self.file_table = self.dynamodb.Table(file_metadata_table)
        self.folder_table = self.dynamodb.Table(folder_metadata_table)
        self.deletion_log_table = self.dynamodb.Table(deletion_log_table)
        self.replacement_table = self.dynamodb.Table(replacement_table)
        self.folder_upload_log_table = self.dynamodb.Table(folder_upload_log_table)
        self.move_log_table = self.dynamodb.Table(move_log_table)
        self.purge_log_table = self.dynamodb.Table(purge_log_table)
        self.resource_guard = ResourceGuard(s3_client=self.s3)
        self.operation_batch_limit = max(int(operation_batch_limit or 100), 1)
        self._bucket_region = None
        self._upload_signing_client = None

    def list_files(self, folder_id: str = ""):
        current_folder = self.get_parent_folder(folder_id)
        breadcrumbs = self.build_breadcrumbs(current_folder)
        base_display_path = self.build_display_prefix(breadcrumbs)
        parent_filter = self.build_parent_folder_filter(folder_id or None)

        folders = []
        partial_errors = {}
        try:
            for folder_metadata in self.scan_table(
                self.folder_table,
                Attr("user_id").eq(self.user_id)
                & parent_filter
                & Attr("status").eq(self.STATUS_ACTIVE),
            ):
                folders.append(self.serialize_folder(folder_metadata, base_display_path))
        except (
            BotoCoreError,
            ClientError,
            ConnectTimeoutError,
            EndpointConnectionError,
            NoCredentialsError,
            NoRegionError,
            ParamValidationError,
            PartialCredentialsError,
            ReadTimeoutError,
        ) as exc:
            result = build_error_result(exc)
            logger.error("Partial list_files error on folders for user [%s]: %s", self.user_id, result.log_detail)
            partial_errors["folders"] = result.ui_detail

        files = []
        try:
            for file_metadata in self.scan_table(
                self.file_table,
                Attr("user_id").eq(self.user_id)
                & parent_filter
                & Attr("status").eq(self.STATUS_ACTIVE),
            ):
                size = self.get_s3_object_size(file_metadata)
                files.append(self.serialize_file(file_metadata, base_display_path, size=size))
        except (
            BotoCoreError,
            ClientError,
            ConnectTimeoutError,
            EndpointConnectionError,
            NoCredentialsError,
            NoRegionError,
            ParamValidationError,
            PartialCredentialsError,
            ReadTimeoutError,
        ) as exc:
            result = build_error_result(exc)
            logger.error("Partial list_files error on files for user [%s]: %s", self.user_id, result.log_detail)
            partial_errors["files"] = result.ui_detail

        folders.sort(key=lambda item: (item.get("name") or "").lower())
        files.sort(key=lambda item: (item.get("name") or "").lower())

        response = {
            "current_folder_id": folder_id,
            "current_path": base_display_path,
            "breadcrumbs": breadcrumbs,
            "folders": folders,
            "files": files,
            "current_folder_state": self.serialize_folder_state(current_folder),
        }
        if partial_errors:
            response["partial_errors"] = partial_errors
        return response

    def list_trashed_files(self):
        trashed_items = []

        for metadata in self.scan_table(
            self.file_table,
            Attr("user_id").eq(self.user_id) & Attr("status").eq("deleted"),
        ):
            trashed_items.append(self.serialize_trashed_file(metadata))

        trashed_items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
        return {
            "files": trashed_items,
        }

    def get_tree(self, root_folder_id: str = "", include_deleted: bool = False):
        normalized_root_id = self.normalize_id(root_folder_id)
        root_folder = self.get_folder(normalized_root_id, require_active=False) if normalized_root_id else None

        if root_folder and root_folder.get("status") not in {self.STATUS_ACTIVE, self.STATUS_DELETED}:
            raise HTTPException(status_code=404, detail="Folder not found")

        breadcrumbs = self.build_breadcrumbs(root_folder)
        root_path = self.build_display_prefix(breadcrumbs)
        root_node = self.build_tree_folder_node(
            root_folder,
            include_deleted=include_deleted,
            base_display_path=root_path,
            is_virtual_root=not root_folder,
        )

        return {
            "root_folder_id": normalized_root_id or "",
            "include_deleted": bool(include_deleted),
            "tree": root_node,
        }

    def start_direct_upload(
        self,
        file_name: str,
        file_size: int,
        file_type: str = "",
        folder_id: str = "",
        replace_existing: bool = False,
        folder_upload_operation_id: str = "",
        folder_upload_root_id: str = "",
    ):
        normalized_file_name = str(file_name or "").strip()
        if not normalized_file_name:
            raise HTTPException(status_code=400, detail="File name is required")

        normalized_file_size = int(file_size or 0)
        if normalized_file_size <= 0:
            raise HTTPException(status_code=400, detail="File size must be greater than 0")

        if self.normalize_id(folder_id):
            if replace_existing:
                self.resource_guard.tables(self.file_table, self.folder_table, self.replacement_table)
            else:
                self.resource_guard.tables(self.file_table, self.folder_table)
        else:
            if replace_existing:
                self.resource_guard.tables(self.file_table, self.replacement_table)
            else:
                self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        parent_folder = self.get_parent_folder(folder_id)
        parent_folder_id = parent_folder["folder_id"] if parent_folder else None
        existing_file = None
        replacement_log = None
        replacing_file_id = ""
        replacement_operation_id = ""

        if replace_existing:
            existing_file = self.find_file_by_name(
                parent_folder_id,
                normalized_file_name,
                statuses={self.STATUS_ACTIVE},
            )
            final_file_name = normalized_file_name
            if existing_file:
                replacement_log = self.create_replacement_log(
                    old_file_id=existing_file["file_id"],
                    new_file_id="",
                    file_name=normalized_file_name,
                )
                replacing_file_id = existing_file["file_id"]
                replacement_operation_id = replacement_log["operation_id"]
                self.mark_file_replacement_pending_delete(existing_file)
                self.update_replacement_log(
                    replacement_log["log_id"],
                    phase="uploading_new",
                    old_file_delete_status=self.REPLACEMENT_DELETE_STATUS_PENDING,
                    old_file_marked_at=self.now_iso(),
                )
        else:
            final_file_name = self.build_upload_file_name(parent_folder_id, normalized_file_name)
        file_id = self.generate_short_id()
        now = datetime.now(timezone.utc)

        metadata = {
            "file_id": file_id,
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder_id),
            "file_name": final_file_name,
            "file_extension": self.get_file_extension(final_file_name),
            "status": self.STATUS_PENDING,
            "created_at": self.iso_from_datetime(now),
            "deleted_at": None,
        }
        if folder_upload_operation_id:
            metadata["folder_upload_operation_id"] = folder_upload_operation_id
            metadata["folder_upload_root_id"] = folder_upload_root_id
            metadata["upload_state"] = self.UPLOAD_STATE_UPLOADING
        object_key = self.build_file_key(metadata)
        self.file_table.put_item(
            Item=metadata,
            ConditionExpression="attribute_not_exists(file_id)",
        )

        if replacement_log:
            self.update_replacement_log(
                replacement_log["log_id"],
                phase="pending_upload_session",
                new_file_id=file_id,
            )

        upload_token = self.encode_upload_token(
            file_id=file_id,
            parent_folder_id=parent_folder_id,
            file_name=final_file_name,
            file_extension=metadata["file_extension"],
            content_type=str(file_type or "").strip(),
            object_key=object_key,
            issued_at=now,
            replacement_log_id=replacement_log["log_id"] if replacement_log else "",
            replacement_operation_id=replacement_operation_id,
            replacing_file_id=replacing_file_id,
            folder_upload_operation_id=folder_upload_operation_id,
            folder_upload_root_id=folder_upload_root_id,
        )

        presigned_post_kwargs = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Conditions": self.build_upload_post_conditions(file_type),
            "ExpiresIn": self.DIRECT_UPLOAD_EXPIRES_SECONDS,
        }
        normalized_file_type = str(file_type or "").strip()
        if normalized_file_type:
            presigned_post_kwargs["Fields"] = {
                "Content-Type": normalized_file_type,
            }

        try:
            presigned_post = self.get_upload_signing_client().generate_presigned_post(**presigned_post_kwargs)
        except Exception:
            self.delete_pending_file_row(file_id)
            if existing_file and replacement_log:
                self.restore_file_from_replacement_pending_delete(existing_file)
                self.update_replacement_log(
                    replacement_log["log_id"],
                    status=self.LOG_STATUS_FAILED,
                    phase="failed",
                    last_error="Could not create upload session",
                    old_file_delete_status=self.REPLACEMENT_DELETE_STATUS_RESTORED,
                )
            raise

        return {
            "file_id": file_id,
            "object_name": final_file_name,
            "upload_token": upload_token,
            "upload_url": presigned_post["url"],
            "upload_fields": presigned_post["fields"],
            "max_upload_bytes": self.MAX_UPLOAD_BYTES,
            "replacement_active": bool(replacing_file_id),
            "replaced_file_id": replacing_file_id,
        }

    def complete_direct_upload(self, upload_token: str):
        payload = self.decode_upload_token(upload_token)
        file_id = self.normalize_id(payload.get("file_id"))
        parent_folder_id = self.normalize_id(payload.get("parent_folder_id")) or None
        file_name = str(payload.get("file_name") or "").strip()
        file_extension = str(payload.get("file_extension") or "").strip()
        content_type = str(payload.get("content_type") or "").strip()
        object_key = str(payload.get("object_key") or "").strip()
        replacement_log_id = self.normalize_id(payload.get("replacement_log_id"))
        replacement_operation_id = self.normalize_id(payload.get("replacement_operation_id"))
        replacing_file_id = self.normalize_id(payload.get("replacing_file_id"))
        folder_upload_operation_id = self.normalize_id(payload.get("folder_upload_operation_id"))
        folder_upload_root_id = self.normalize_id(payload.get("folder_upload_root_id"))

        if replacing_file_id:
            self.resource_guard.tables(self.file_table, self.replacement_table, self.deletion_log_table)
        else:
            self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)

        if not file_id or not file_name or not object_key:
            raise HTTPException(status_code=400, detail="Upload token is missing required file metadata")

        try:
            head_response = self.s3.head_object(
                Bucket=self.bucket,
                Key=object_key,
            )
        except ClientError as exc:
            raise HTTPException(status_code=400, detail="Uploaded file not found in S3") from exc

        content_length = int(head_response.get("ContentLength") or 0)
        if content_length <= 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if content_length > self.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Uploaded file exceeds the 1GB limit")

        metadata = self.get_file(file_id, require_active=False)
        if metadata.get("status") == self.STATUS_ACTIVE:
            raise HTTPException(status_code=409, detail="Upload session has already been completed")
        if metadata.get("status") != self.STATUS_PENDING:
            raise HTTPException(status_code=400, detail="Upload session is not in a pending state")

        if content_type and head_response.get("ContentType") and head_response.get("ContentType") != content_type:
            raise HTTPException(status_code=400, detail="Uploaded file content type does not match the upload session")

        replacement_metadata = None
        replacement_succeeded = False
        new_file_activated = False
        if replacing_file_id:
            replacement_metadata = self.get_file(replacing_file_id, require_active=False)
            if replacement_metadata.get("status") != self.STATUS_REPLACEMENT_PENDING_DELETE:
                raise HTTPException(status_code=409, detail="Replacement session is no longer valid")

        try:
            if replacement_log_id:
                self.update_replacement_log(
                    replacement_log_id,
                    phase="finalizing_new",
                )
            self.mark_upload_file_active(
                file_id,
                file_name,
                file_extension,
                folder_upload_operation_id=folder_upload_operation_id,
                folder_upload_root_id=folder_upload_root_id,
            )
            new_file_activated = True
            if replacing_file_id and replacement_metadata:
                if replacement_log_id:
                    self.update_replacement_log(
                        replacement_log_id,
                        phase="deleting_old",
                    )
                self.hard_delete_replaced_file(
                    replacement_metadata,
                    operation_id=replacement_operation_id,
                )
                if replacement_log_id:
                    self.update_replacement_log(
                        replacement_log_id,
                        status=self.LOG_STATUS_DONE,
                        phase="completed",
                        new_file_id=file_id,
                        old_file_delete_status=self.REPLACEMENT_DELETE_STATUS_DELETED,
                        old_file_deleted_at=self.now_iso(),
                        completed_at=self.now_iso(),
                        last_error="",
                    )
                replacement_succeeded = True
            else:
                self.increment_children_count(parent_folder_id)
        except Exception as exc:
            if replacing_file_id and replacement_metadata and not new_file_activated:
                self.rollback_replacement_upload(
                    new_file_metadata=metadata,
                    old_file_metadata=replacement_metadata,
                    object_key=object_key,
                )
            if replacement_log_id:
                self.update_replacement_log(
                    replacement_log_id,
                    status=self.LOG_STATUS_FAILED,
                    phase="failed",
                    last_error=str(exc),
                    new_file_id=file_id,
                    old_file_delete_status=(
                        self.REPLACEMENT_DELETE_STATUS_FAILED
                        if new_file_activated
                        else self.REPLACEMENT_DELETE_STATUS_RESTORED
                    ),
                )
            if replacing_file_id and new_file_activated:
                raise HTTPException(
                    status_code=409,
                    detail="New file uploaded, but old file cleanup failed. Check the replacement log before retrying.",
                ) from exc
            raise HTTPException(status_code=409, detail="Upload session could not be finalized") from exc

        return {
            "uploaded": file_id,
            "object_name": file_name,
            "replacement_completed": replacement_succeeded,
            "replacement_operation_id": replacement_operation_id,
        }

    def create_folder(
        self,
        parent_id: str,
        name: str,
        *,
        folder_upload_operation_id: str = "",
        folder_upload_root_id: str = "",
    ):
        self.resource_guard.tables(self.folder_table)
        self.resource_guard.s3_bucket(self.bucket)
        parent_folder = self.get_parent_folder(parent_id)
        normalized_name = name.strip().strip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Folder name is required")
        if self.has_active_folder_with_name(parent_folder["folder_id"] if parent_folder else None, normalized_name):
            raise HTTPException(status_code=400, detail="An active folder with this name already exists")

        if folder_upload_operation_id:
            metadata = self.create_folder_metadata_for_upload(
                parent_folder_id=parent_folder["folder_id"] if parent_folder else None,
                folder_name=normalized_name,
                operation_id=folder_upload_operation_id,
                root_folder_id=folder_upload_root_id,
            )
            return {
                "created_folder_id": metadata["folder_id"],
                "created_folder_name": normalized_name,
            }

        folder_id = self.generate_short_id()
        now = self.now_iso()
        metadata = {
            "folder_id": folder_id,
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder["folder_id"] if parent_folder else None),
            "folder_name": normalized_name,
            "status": self.STATUS_PENDING,
            "created_at": now,
            "deleted_at": None,
            "children_count": 0,
        }

        self.folder_table.put_item(
            Item=metadata,
            ConditionExpression="attribute_not_exists(folder_id)",
        )
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self.build_folder_key(metadata),
                Body=b"",
            )
            self.folder_table.update_item(
                Key={"folder_id": folder_id},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": self.STATUS_ACTIVE,
                    ":pending_status": self.STATUS_PENDING,
                },
                ConditionExpression="attribute_exists(folder_id) AND #status = :pending_status",
            )
            self.increment_children_count(parent_folder["folder_id"] if parent_folder else None)
        except Exception:
            self.rollback_pending_folder_row(metadata)
            raise

        return {
            "created_folder_id": folder_id,
            "created_folder_name": normalized_name,
        }

    def create_folder_metadata_for_upload(
        self,
        *,
        parent_folder_id: str | None,
        folder_name: str,
        operation_id: str,
        root_folder_id: str,
        upload_state: str = "",
        upload_warning: str = "",
    ):
        metadata = {
            "folder_id": self.generate_short_id(),
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder_id),
            "folder_name": folder_name,
            "status": self.STATUS_ACTIVE,
            "created_at": self.now_iso(),
            "deleted_at": None,
            "children_count": 0,
            "folder_upload_operation_id": operation_id,
            "folder_upload_root_id": root_folder_id,
        }
        if upload_state:
            metadata["upload_state"] = upload_state
        if upload_warning:
            metadata["upload_warning"] = upload_warning

        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_folder_key(metadata),
            Body=b"",
        )
        self.folder_table.put_item(
            Item=metadata,
            ConditionExpression="attribute_not_exists(folder_id)",
        )
        self.increment_children_count(parent_folder_id)
        return metadata

    def delete_pending_file_row(self, file_id: str):
        try:
            self.file_table.delete_item(
                Key={"file_id": file_id},
                ConditionExpression="attribute_exists(file_id)",
            )
        except Exception as exc:
            logger.error("Failed to delete pending file row [%s]: %s", file_id, exc)

    def mark_upload_file_active(
        self,
        file_id: str,
        file_name: str,
        file_extension: str,
        *,
        folder_upload_operation_id: str = "",
        folder_upload_root_id: str = "",
    ):
        update_expression = "SET file_name = :file_name, file_extension = :file_extension, #status = :status, deleted_at = :deleted_at"
        expression_attribute_values = {
            ":file_name": file_name,
            ":file_extension": file_extension or self.get_file_extension(file_name),
            ":status": self.STATUS_ACTIVE,
            ":deleted_at": None,
            ":pending_status": self.STATUS_PENDING,
        }
        if folder_upload_operation_id:
            update_expression += ", upload_state = :upload_state, folder_upload_operation_id = :folder_upload_operation_id, folder_upload_root_id = :folder_upload_root_id"
            expression_attribute_values[":upload_state"] = self.UPLOAD_STATE_PENDING_FINALIZE
            expression_attribute_values[":folder_upload_operation_id"] = folder_upload_operation_id
            expression_attribute_values[":folder_upload_root_id"] = folder_upload_root_id

        self.file_table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression="attribute_exists(file_id) AND #status = :pending_status",
        )

    def mark_file_replacement_pending_delete(self, metadata):
        self.file_table.update_item(
            Key={"file_id": metadata["file_id"]},
            UpdateExpression="SET #status = :status, to_be_deleted = :to_be_deleted",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_REPLACEMENT_PENDING_DELETE,
                ":to_be_deleted": True,
                ":active_status": self.STATUS_ACTIVE,
            },
            ConditionExpression="attribute_exists(file_id) AND #status = :active_status",
        )

    def restore_file_from_replacement_pending_delete(self, metadata):
        self.file_table.update_item(
            Key={"file_id": metadata["file_id"]},
            UpdateExpression="SET #status = :status, to_be_deleted = :to_be_deleted",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_ACTIVE,
                ":to_be_deleted": False,
                ":replacement_status": self.STATUS_REPLACEMENT_PENDING_DELETE,
            },
            ConditionExpression="attribute_exists(file_id) AND #status = :replacement_status",
        )

    def delete_replaced_file(self, metadata):
        self.s3.delete_object(
            Bucket=self.bucket,
            Key=self.build_file_key(metadata),
        )
        self.file_table.delete_item(
            Key={"file_id": metadata["file_id"]},
            ConditionExpression="attribute_exists(file_id)",
        )

    def hard_delete_replaced_file(self, metadata, *, operation_id: str):
        file_id = str(metadata.get("file_id") or "").strip()
        parent_folder_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
        deletion_log = self.create_deletion_log(
            root_id=file_id,
            root_kind="file",
            root_parent_id=parent_folder_id,
            trigger_operation="replacement_delete",
            operation_id=operation_id,
        )

        try:
            self.mark_file_deletion_pending(file_id, file_id)
            self.update_deletion_log(
                deletion_log["log_id"],
                phase="deleting_s3",
            )
            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.build_file_key(metadata),
            )
            self.update_deletion_log(
                deletion_log["log_id"],
                phase="removing_db_entry",
            )
            self.file_table.delete_item(
                Key={"file_id": file_id},
                ConditionExpression="attribute_exists(file_id)",
            )
            self.update_deletion_log(
                deletion_log["log_id"],
                status=self.LOG_STATUS_DONE,
                phase="completed",
                deleted_files_count=1,
                completed_at=self.now_iso(),
            )
        except Exception as exc:
            self.update_deletion_log(
                deletion_log["log_id"],
                status=self.LOG_STATUS_FAILED,
                phase="failed",
                last_error=str(exc),
            )
            raise

    def rollback_replacement_upload(self, *, new_file_metadata, old_file_metadata, object_key: str):
        new_file_id = str(new_file_metadata.get("file_id") or "").strip()
        if object_key:
            try:
                self.s3.delete_object(
                    Bucket=self.bucket,
                    Key=object_key,
                )
            except Exception as exc:
                logger.error("Failed to roll back replacement S3 object for file [%s]: %s", new_file_id, exc)

        if new_file_id:
            try:
                self.file_table.delete_item(
                    Key={"file_id": new_file_id},
                    ConditionExpression="attribute_exists(file_id)",
                )
            except Exception as exc:
                logger.error("Failed to roll back replacement file row [%s]: %s", new_file_id, exc)

        try:
            self.restore_file_from_replacement_pending_delete(old_file_metadata)
        except Exception as exc:
            logger.error("Failed to restore replacement source file [%s]: %s", old_file_metadata.get("file_id"), exc)

    def rollback_pending_folder_row(self, metadata):
        folder_id = str(metadata.get("folder_id") or "").strip()
        if not folder_id:
            return

        try:
            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.build_folder_key(metadata),
            )
        except Exception as exc:
            logger.error("Failed to roll back S3 folder object for pending folder [%s]: %s", folder_id, exc)

        try:
            self.folder_table.delete_item(
                Key={"folder_id": folder_id},
                ConditionExpression="attribute_exists(folder_id)",
            )
        except Exception as exc:
            logger.error("Failed to delete pending folder row [%s]: %s", folder_id, exc)

    def get_download_url(self, file_id: str):
        self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        metadata = self.get_file(file_id, require_active=True)
        filename = metadata["file_name"] or "download"
        url = self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self.build_file_key(metadata),
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=60,
        )

        return {
            "file_id": file_id,
            "url": url,
            "expires_in": 60,
        }

    def get_preview_url(self, file_id: str):
        self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        metadata = self.get_file(file_id, require_active=True)
        url = self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self.build_file_key(metadata),
            },
            ExpiresIn=60,
        )

        return {
            "file_id": file_id,
            "file_name": metadata.get("file_name") or "",
            "url": url,
            "expires_in": 60,
        }

    def get_preview_text(self, file_id: str):
        self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        metadata = self.get_file(file_id, require_active=True)

        response = self.s3.get_object(
            Bucket=self.bucket,
            Key=self.build_file_key(metadata),
        )
        raw_body = response.get("Body")
        content_bytes = raw_body.read() if raw_body is not None else b""

        return {
            "file_id": file_id,
            "file_name": metadata.get("file_name") or "",
            "content": content_bytes.decode("utf-8", errors="replace"),
        }

    def get_preview_binary(self, file_id: str):
        self.resource_guard.tables(self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        metadata = self.get_file(file_id, require_active=True)

        response = self.s3.get_object(
            Bucket=self.bucket,
            Key=self.build_file_key(metadata),
        )
        raw_body = response.get("Body")
        content_bytes = raw_body.read() if raw_body is not None else b""

        return {
            "file_id": file_id,
            "file_name": metadata.get("file_name") or "",
            "content_base64": base64.b64encode(content_bytes).decode("ascii"),
        }

    def rename_file(self, file_id: str, new_name: str):
        self.resource_guard.tables(self.file_table, self.folder_table)
        metadata = self.get_file(file_id, require_active=True)
        final_name = self.build_renamed_name(new_name, metadata.get("file_extension", ""))

        self.file_table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="SET file_name = :file_name, file_extension = :file_extension",
            ExpressionAttributeValues={
                ":file_name": final_name,
                ":file_extension": self.get_file_extension(final_name),
            },
            ConditionExpression="attribute_exists(file_id)",
        )

        return {
            "file_id": file_id,
            "source_name": metadata["file_name"],
            "renamed_name": final_name,
        }

    def delete_object(self, entry_id: str):
        self.resource_guard.tables(self.file_table, self.folder_table)
        file_metadata = self.get_file_or_none(entry_id)
        if file_metadata and file_metadata.get("user_id") == self.user_id:
            return self.soft_delete_file(file_metadata)

        folder_metadata = self.get_folder_or_none(entry_id)
        if folder_metadata and folder_metadata.get("user_id") == self.user_id:
            return self.soft_delete_folder(folder_metadata)

        raise HTTPException(status_code=404, detail="Object not found")

    def get_purge_state(self):
        self.resource_guard.tables(self.purge_log_table)
        metadata = self.get_active_purge_log()
        if not metadata:
            return {
                "purge_active": False,
                "root_id": "",
                "root_parent_folder_id": "",
                "phase": "idle",
            }

        return {
            "purge_active": metadata.get("status") in {self.LOG_STATUS_RUNNING, self.LOG_STATUS_FAILED},
            "root_id": self.get_log_root_id(metadata),
            "root_parent_folder_id": str(metadata.get("root_parent_folder_id") or "").strip(),
            "phase": str(metadata.get("phase") or "idle").strip() or "idle",
            "log_id": str(metadata.get("log_id") or "").strip(),
            "operation_id": str(metadata.get("operation_id") or "").strip(),
            "status": str(metadata.get("status") or "").strip(),
            "root_kind": self.get_log_root_kind(metadata),
            "preceding_log_id": str(metadata.get("preceding_log_id") or "").strip(),
            "is_resumed": bool(metadata.get("is_resumed")),
        }

    def get_move_state(self):
        self.resource_guard.tables(self.move_log_table)
        metadata = self.get_active_move_log()
        if not metadata:
            return {
                "move_active": False,
                "log_id": "",
                "operation_id": "",
                "source_id": "",
                "destination_folder_id": "",
                "phase": "idle",
                "mode": "",
                "source_kind": "",
            }

        return {
            "move_active": metadata.get("status") in {self.LOG_STATUS_RUNNING, self.LOG_STATUS_FAILED},
            "log_id": str(metadata.get("log_id") or "").strip(),
            "operation_id": str(metadata.get("operation_id") or "").strip(),
            "source_id": str(metadata.get("source_entry_id") or "").strip(),
            "destination_folder_id": str(metadata.get("destination_folder_id") or "").strip(),
            "phase": str(metadata.get("phase") or "idle").strip() or "idle",
            "mode": str(metadata.get("move_mode") or "").strip(),
            "source_kind": str(metadata.get("source_entry_kind") or "").strip(),
            "preceding_log_id": str(metadata.get("preceding_log_id") or "").strip(),
            "is_resumed": bool(metadata.get("is_resumed")),
        }

    def move_entry(self, source_id: str = "", destination_folder_id: str = "", mode: str = MOVE_MODE_MERGE):
        self.resource_guard.tables(
            self.file_table,
            self.folder_table,
            self.move_log_table,
            self.purge_log_table,
        )
        self.resource_guard.s3_bucket(self.bucket)
        active_delete_log = self.get_active_purge_log()
        if active_delete_log:
            active_root_id = self.get_log_root_id(active_delete_log)
            raise HTTPException(
                status_code=409,
                detail=f"A purge is already in progress for folder '{active_root_id}'. Finish that purge before moving.",
            )

        active_move_log = self.get_active_move_log()
        if active_move_log:
            normalized_source_id = self.normalize_id(source_id)
            active_source_id = self.normalize_id(active_move_log.get("source_entry_id"))
            if normalized_source_id and active_source_id and normalized_source_id != active_source_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"A move is already in progress for entry '{active_source_id}'. Resume that move first.",
                )
            return self.resume_move(active_move_log)

        normalized_source_id = self.normalize_id(source_id)
        if not normalized_source_id:
            raise HTTPException(status_code=400, detail="Source id is required")

        normalized_destination_id = self.normalize_id(destination_folder_id) or None
        normalized_mode = self.normalize_move_mode(mode)
        source_metadata, source_kind = self.get_entry_for_move(normalized_source_id)

        if normalized_destination_id:
            self.get_folder(normalized_destination_id, require_active=True)

        if source_kind == "folder":
            self.ensure_destination_is_not_inside_source(source_metadata["folder_id"], normalized_destination_id)
            source_parent_id = self.from_storage_parent_folder_id(source_metadata.get("parent_folder_id"))
            if normalized_destination_id == source_parent_id:
                raise HTTPException(status_code=400, detail="Source folder is already inside that destination")
        else:
            source_parent_id = self.from_storage_parent_folder_id(source_metadata.get("parent_folder_id"))
            if normalized_destination_id and normalized_destination_id == source_parent_id:
                raise HTTPException(status_code=400, detail="Source is already inside that folder")

        log_metadata = self.create_move_log(
            operation_id=self.generate_short_id(),
            source_id=normalized_source_id,
            source_kind=source_kind,
            source_parent_id=source_parent_id,
            destination_folder_id=normalized_destination_id,
            mode=normalized_mode,
        )
        return self.run_move(log_metadata, resumed=False)

    def resume_move(self, log_metadata=None):
        self.resource_guard.tables(
            self.file_table,
            self.folder_table,
            self.move_log_table,
            self.purge_log_table,
        )
        self.resource_guard.s3_bucket(self.bucket)
        log_metadata = log_metadata or self.get_active_move_log()
        if not log_metadata:
            raise HTTPException(status_code=400, detail="No move is in progress")
        resume_log = self.create_resume_move_log(log_metadata)
        return self.run_move(resume_log, resumed=True)

    def run_move(self, log_metadata, resumed: bool):
        source_id = self.normalize_id(log_metadata.get("source_entry_id"))
        source_kind = str(log_metadata.get("source_entry_kind") or "").strip()
        destination_folder_id = self.normalize_id(log_metadata.get("destination_folder_id")) or None
        move_mode = self.normalize_move_mode(log_metadata.get("move_mode"))
        summary = {
            "copied_files": int(log_metadata.get("moved_files_count") or 0),
            "copied_folders": int(log_metadata.get("moved_folders_count") or 0),
            "purged_files": int(log_metadata.get("purged_files_count") or 0),
            "purged_folders": int(log_metadata.get("purged_folders_count") or 0),
        }

        source_metadata, _ = self.get_entry_for_move(source_id)
        source_parent_id = self.from_storage_parent_folder_id(source_metadata.get("parent_folder_id"))

        try:
            phase = str(log_metadata.get("phase") or "copying").strip() or "copying"
            batch_limit = self.get_log_batch_limit(log_metadata)
            budget = self.create_processing_budget(batch_limit)
            if phase == "copying":
                self.update_move_log(
                    log_metadata["log_id"],
                    status=self.LOG_STATUS_RUNNING,
                    phase="copying",
                    last_error="",
                )
                if source_kind == "folder":
                    self.move_folder_subtree(
                        source_metadata,
                        destination_folder_id,
                        move_mode,
                        summary,
                        budget,
                        operation_id=str(log_metadata.get("operation_id") or "").strip(),
                        is_root=True,
                    )
                else:
                    self.move_single_file(
                        source_metadata,
                        destination_folder_id,
                        move_mode,
                        summary,
                        budget,
                        operation_id=str(log_metadata.get("operation_id") or "").strip(),
                        is_root=True,
                    )
                phase = "completed" if budget["complete"] else "copying"

                if not budget["complete"]:
                    self.update_move_log(
                        log_metadata["log_id"],
                        status=self.LOG_STATUS_RUNNING,
                        phase="copying",
                        last_error="",
                        moved_files_count=summary["copied_files"],
                        moved_folders_count=summary["copied_folders"],
                        processed_this_run=budget["processed"],
                    )
                    return {
                        "source_entry_id": source_id,
                        "source_entry_kind": source_kind,
                        "destination_folder_id": destination_folder_id or "",
                        "mode": move_mode,
                        "resumed": resumed,
                        "moved_files": summary["copied_files"],
                        "moved_folders": summary["copied_folders"],
                        "phase": "copying",
                        "has_more": True,
                        "batch_limit": batch_limit,
                        "processed_this_run": budget["processed"],
                    }

            self.update_move_log(
                log_metadata["log_id"],
                status=self.LOG_STATUS_DONE,
                phase="completed",
                last_error="",
                moved_files_count=summary["copied_files"],
                moved_folders_count=summary["copied_folders"],
                processed_this_run=budget["processed"],
                completed_at=self.now_iso(),
            )
        except Exception as exc:
            next_phase = phase if phase == "completed" else "copying"
            self.update_move_log(
                log_metadata["log_id"],
                status=self.LOG_STATUS_FAILED,
                phase=next_phase,
                last_error=str(exc),
                moved_files_count=summary["copied_files"],
                moved_folders_count=summary["copied_folders"],
                processed_this_run=budget["processed"] if "budget" in locals() else 0,
            )
            raise

        purge_root_id, purge_root_kind = self.resolve_original_move_root(
            log_metadata=log_metadata,
            source_kind=source_kind,
            fallback_root_id=source_id,
        )
        purge_log = self.create_purge_log(
            operation_id=str(log_metadata.get("operation_id") or "").strip(),
            root_id=purge_root_id,
            root_kind=purge_root_kind,
            root_parent_id=source_parent_id,
            preceding_log_id=log_metadata["log_id"],
            trigger_operation=self.LOG_OPERATION_MOVE,
        )
        purge_result = self.run_purge(purge_log, resumed=False)

        return {
            "source_entry_id": source_id,
            "source_entry_kind": source_kind,
            "destination_folder_id": destination_folder_id or "",
            "mode": move_mode,
            "resumed": resumed,
            "moved_files": summary["copied_files"],
            "moved_folders": summary["copied_folders"],
            "phase": "completed",
            "purge_log_id": str(purge_log.get("log_id") or "").strip(),
            "purged_files": int(purge_result.get("deleted_files") or 0),
            "purged_folders": int(purge_result.get("deleted_folders") or 0),
            "has_more": bool(purge_result.get("has_more")),
            "batch_limit": batch_limit,
            "processed_this_run": budget["processed"],
        }

    def purge_folder(self, folder_id: str = ""):
        self.resource_guard.tables(self.file_table, self.folder_table, self.purge_log_table)
        self.resource_guard.s3_bucket(self.bucket)
        normalized_folder_id = self.normalize_id(folder_id)
        active_log = self.get_active_purge_log()
        active_root_id = self.get_log_root_id(active_log) if active_log else ""

        if active_log:
            if normalized_folder_id and active_root_id and normalized_folder_id != active_root_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"A purge is already in progress for folder '{active_root_id}'. Resume that purge first.",
                )
            if not active_root_id:
                raise HTTPException(status_code=409, detail="Purge is marked active but has no root id")
            return self.resume_purge(active_log)

        if not normalized_folder_id:
            raise HTTPException(status_code=400, detail="Folder id is required")

        root_metadata = self.get_folder(normalized_folder_id, require_active=False)
        root_parent_id = self.from_storage_parent_folder_id(root_metadata.get("parent_folder_id"))
        log_metadata = self.create_purge_log(
            operation_id=self.generate_short_id(),
            root_id=normalized_folder_id,
            root_kind="folder",
            root_parent_id=root_parent_id,
            preceding_log_id="",
            trigger_operation="manual_purge",
        )
        return self.run_purge(log_metadata, resumed=False)

    def resume_purge(self, log_metadata=None):
        self.resource_guard.tables(self.file_table, self.folder_table, self.purge_log_table)
        self.resource_guard.s3_bucket(self.bucket)
        log_metadata = log_metadata or self.get_active_purge_log()
        if not log_metadata:
            raise HTTPException(status_code=400, detail="No purge is in progress")
        resume_log = self.create_resume_purge_log(log_metadata)
        return self.run_purge(resume_log, resumed=True)

    def run_purge(self, log_metadata, resumed: bool):
        root_id = self.get_log_root_id(log_metadata)
        root_kind = self.get_log_root_kind(log_metadata)
        root_parent_id = self.normalize_id(log_metadata.get("root_parent_folder_id")) or None
        batch_limit = self.get_log_batch_limit(log_metadata)
        budget = self.create_processing_budget(batch_limit)
        summary = {
            "deleted_files": 0,
            "deleted_folders": 0,
        }
        if str(log_metadata.get("deleted_files_count") or "").strip():
            summary["deleted_files"] = int(log_metadata.get("deleted_files_count") or 0)
        if str(log_metadata.get("deleted_folders_count") or "").strip():
            summary["deleted_folders"] = int(log_metadata.get("deleted_folders_count") or 0)

        try:
            phase = str(log_metadata.get("phase") or "deleting").strip() or "deleting"
            self.update_purge_log(
                log_metadata["log_id"],
                status=self.LOG_STATUS_RUNNING,
                phase="deleting",
                last_error="",
            )
            if root_kind == "file":
                root_metadata = self.get_file_or_none(root_id)
                if root_metadata and root_metadata.get("user_id") == self.user_id:
                    if not self.purge_file_entry(root_metadata, root_id, budget):
                        self.update_purge_log(
                            log_metadata["log_id"],
                            status=self.LOG_STATUS_RUNNING,
                            phase="deleting",
                            last_error="",
                            deleted_files_count=summary["deleted_files"],
                            deleted_folders_count=summary["deleted_folders"],
                            processed_this_run=budget["processed"],
                        )
                        return {
                            "root_folder_id": root_id,
                            "root_entry_id": root_id,
                            "root_entry_kind": root_kind,
                            "resumed": resumed,
                            "deleted_files": summary["deleted_files"],
                            "deleted_folders": summary["deleted_folders"],
                            "phase": "deleting",
                            "has_more": True,
                            "batch_limit": batch_limit,
                            "processed_this_run": budget["processed"],
                        }
                    summary["deleted_files"] += 1
            else:
                root_metadata = self.get_folder_or_none(root_id)
                if root_metadata and root_metadata.get("user_id") == self.user_id:
                    complete = self.delete_folder_subtree(root_metadata, root_id, summary, budget)
                    if not complete:
                        self.update_purge_log(
                            log_metadata["log_id"],
                            status=self.LOG_STATUS_RUNNING,
                            phase="deleting",
                            last_error="",
                            deleted_files_count=summary["deleted_files"],
                            deleted_folders_count=summary["deleted_folders"],
                            processed_this_run=budget["processed"],
                        )
                        return {
                            "root_folder_id": root_id,
                            "root_entry_id": root_id,
                            "root_entry_kind": root_kind,
                            "resumed": resumed,
                            "deleted_files": summary["deleted_files"],
                            "deleted_folders": summary["deleted_folders"],
                            "phase": "deleting",
                            "has_more": True,
                            "batch_limit": batch_limit,
                            "processed_this_run": budget["processed"],
                        }

            return self.finalize_purge(
                root_id,
                root_parent_id,
                root_kind=root_kind,
                resumed=resumed,
                summary=summary,
                log_metadata=log_metadata,
            )
        except Exception as exc:
            self.update_purge_log(
                log_metadata["log_id"],
                status=self.LOG_STATUS_FAILED,
                phase="deleting",
                last_error=str(exc),
                deleted_files_count=summary["deleted_files"],
                deleted_folders_count=summary["deleted_folders"],
                processed_this_run=budget["processed"],
            )
            raise

    def finalize_purge(
        self,
        root_id: str,
        root_parent_id: str | None,
        *,
        root_kind: str,
        resumed: bool,
        summary: dict | None = None,
        log_metadata=None,
    ):
        summary = summary or {
            "deleted_files": 0,
            "deleted_folders": 0,
        }

        if root_parent_id:
            self.reconcile_folder_children_count_chain(root_parent_id)

        self.update_purge_log(
            log_metadata["log_id"],
            status=self.LOG_STATUS_DONE,
            phase="completed",
            last_error="",
            deleted_files_count=summary["deleted_files"],
            deleted_folders_count=summary["deleted_folders"],
            completed_at=self.now_iso(),
        )

        return {
            "root_folder_id": root_id,
            "root_entry_id": root_id,
            "root_entry_kind": root_kind,
            "resumed": resumed,
            "deleted_files": summary["deleted_files"],
            "deleted_folders": summary["deleted_folders"],
            "phase": "completed",
        }

    def delete_folder_subtree(self, folder_metadata, root_id: str, summary: dict, budget: dict):
        normalized_parent_folder_id = self.from_storage_parent_folder_id(folder_metadata.get("parent_folder_id"))
        next_folder_metadata = dict(folder_metadata)
        next_folder_metadata["parent_folder_id"] = normalized_parent_folder_id

        child_folders, child_files = self.list_direct_children(folder_metadata["folder_id"])

        for child_folder in child_folders:
            if not self.delete_folder_subtree(child_folder, root_id, summary, budget):
                return False

        for child_file in child_files:
            if not self.purge_file_entry(child_file, root_id, budget):
                return False
            summary["deleted_files"] += 1

        if not self.consume_processing_budget(budget):
            return False
        self.mark_folder_deletion_pending(folder_metadata["folder_id"], root_id)
        self.hard_delete_folder(next_folder_metadata)
        summary["deleted_folders"] += 1
        return True

    def purge_moved_source(self, source_kind: str, source_id: str, summary: dict, budget: dict):
        if source_kind == "file":
            source_file = self.get_file_or_none(source_id)
            if source_file and source_file.get("user_id") == self.user_id:
                if not self.purge_file_entry(source_file, source_id, budget):
                    return False
                summary["purged_files"] += 1
            return True

        source_folder = self.get_folder_or_none(source_id)
        if source_folder and source_folder.get("user_id") == self.user_id:
            purge_summary = {
                "deleted_files": summary["purged_files"],
                "deleted_folders": summary["purged_folders"],
            }
            if not self.delete_folder_subtree(source_folder, source_id, purge_summary, budget):
                return False
            summary["purged_files"] = purge_summary["deleted_files"]
            summary["purged_folders"] = purge_summary["deleted_folders"]
        return True

    def purge_file_entry(self, metadata, root_id: str, budget: dict):
        if not self.consume_processing_budget(budget):
            return False
        self.mark_file_deletion_pending(metadata["file_id"], root_id)
        self.s3.delete_object(
            Bucket=self.bucket,
            Key=self.build_file_key(metadata),
        )
        self.file_table.delete_item(
            Key={"file_id": metadata["file_id"]},
            ConditionExpression="attribute_exists(file_id)",
        )
        return True

    def list_direct_children(self, folder_id: str):
        child_folders = [
            item
            for item in self.query_items_by_parent(self.folder_table, folder_id)
            if item.get("user_id") == self.user_id
        ]
        child_files = [
            item
            for item in self.query_items_by_parent(self.file_table, folder_id)
            if item.get("user_id") == self.user_id
        ]

        child_folders.sort(key=lambda item: ((item.get("folder_name") or "").lower(), item.get("folder_id") or ""))
        child_files.sort(key=lambda item: ((item.get("file_name") or "").lower(), item.get("file_id") or ""))
        return child_folders, child_files

    def build_tree_folder_node(
        self,
        folder_metadata,
        *,
        include_deleted: bool,
        base_display_path: str,
        is_virtual_root: bool = False,
    ):
        if is_virtual_root:
            folder_id = ""
            folder_name = "Root"
            folder_status = self.STATUS_ACTIVE
        else:
            folder_id = folder_metadata["folder_id"]
            folder_name = folder_metadata["folder_name"]
            folder_status = str(folder_metadata.get("status") or self.STATUS_ACTIVE)

        child_folders_metadata, child_files_metadata = self.list_direct_children(folder_id)
        children = []

        for child_folder in child_folders_metadata:
            status = str(child_folder.get("status") or self.STATUS_ACTIVE)
            if status not in {self.STATUS_ACTIVE, self.STATUS_DELETED}:
                continue
            if not include_deleted and status != self.STATUS_ACTIVE:
                continue
            child_path = f"{base_display_path}{child_folder['folder_name']}/"
            children.append(
                self.build_tree_folder_node(
                    child_folder,
                    include_deleted=include_deleted,
                    base_display_path=child_path,
                )
            )

        for child_file in child_files_metadata:
            status = str(child_file.get("status") or self.STATUS_ACTIVE)
            if status not in {self.STATUS_ACTIVE, self.STATUS_DELETED}:
                continue
            if not include_deleted and status != self.STATUS_ACTIVE:
                continue
            children.append(self.build_tree_file_node(child_file, base_display_path=base_display_path))

        return {
            "id": folder_id,
            "kind": "folder",
            "name": folder_name,
            "status": folder_status,
            "upload_state": "" if is_virtual_root else str(folder_metadata.get("upload_state") or ""),
            "upload_warning": "" if is_virtual_root else str(folder_metadata.get("upload_warning") or ""),
            "path": base_display_path if is_virtual_root else f"{base_display_path}",
            "children": children,
        }

    def build_tree_file_node(self, file_metadata, *, base_display_path: str):
        return {
            "id": file_metadata["file_id"],
            "kind": "file",
            "name": file_metadata["file_name"],
            "status": str(file_metadata.get("status") or self.STATUS_ACTIVE),
            "upload_state": str(file_metadata.get("upload_state") or ""),
            "path": f"{base_display_path}{file_metadata['file_name']}",
            "children": [],
        }

    def move_folder_subtree(
        self,
        source_folder_metadata,
        destination_parent_id: str | None,
        move_mode: str,
        summary: dict,
        budget: dict,
        *,
        operation_id: str,
        is_root: bool,
    ):
        if source_folder_metadata.get("status") == self.STATUS_MOVED:
            target_folder_id = (
                self.normalize_id(source_folder_metadata.get("moved_target_folder_id"))
                or self.normalize_id(source_folder_metadata.get("move_target_folder_id"))
                or None
            )
        else:
            target_folder_id = self.normalize_id(source_folder_metadata.get("move_target_folder_id")) or None
            if not target_folder_id:
                target_folder = self.resolve_target_folder_for_move(
                    source_folder_metadata,
                    destination_parent_id,
                    move_mode,
                    is_root=is_root,
                )
                target_folder_id = target_folder["folder_id"]
                self.mark_folder_move_target(source_folder_metadata["folder_id"], target_folder_id, operation_id)

        child_folders, child_files = self.list_direct_children(source_folder_metadata["folder_id"])

        for child_folder in child_folders:
            if child_folder.get("status") == self.STATUS_MOVED:
                continue
            complete = self.move_folder_subtree(
                child_folder,
                target_folder_id,
                move_mode,
                summary,
                budget,
                operation_id=operation_id,
                is_root=False,
            )
            if not complete:
                return False

        for child_file in child_files:
            if child_file.get("status") == self.STATUS_MOVED:
                continue
            complete = self.move_single_file(
                child_file,
                target_folder_id,
                move_mode,
                summary,
                budget,
                operation_id=operation_id,
                is_root=False,
            )
            if not complete:
                return False

        if source_folder_metadata.get("status") != self.STATUS_MOVED:
            if not self.consume_processing_budget(budget):
                return False
            self.mark_folder_moved(source_folder_metadata["folder_id"], target_folder_id)
            summary["copied_folders"] += 1

        return True

    def move_single_file(
        self,
        source_file_metadata,
        destination_parent_id: str | None,
        move_mode: str,
        summary: dict,
        budget: dict,
        *,
        operation_id: str,
        is_root: bool,
    ):
        if source_file_metadata.get("status") == self.STATUS_MOVED:
            return True

        if not self.consume_processing_budget(budget):
            return False

        target_metadata = self.create_target_file_for_move(
            source_file_metadata,
            destination_parent_id,
            move_mode,
            is_root=is_root,
        )
        self.copy_file_in_s3(source_file_metadata, target_metadata)
        self.file_table.put_item(Item=target_metadata)
        self.increment_children_count(destination_parent_id)
        self.mark_file_moved(source_file_metadata["file_id"], target_metadata["file_id"], operation_id)
        summary["copied_files"] += 1
        return True

    def resolve_target_folder_for_move(
        self,
        source_folder_metadata,
        destination_parent_id: str | None,
        move_mode: str,
        *,
        is_root: bool,
    ):
        requested_name = str(source_folder_metadata.get("folder_name") or "").strip()
        source_folder_id = self.normalize_id(source_folder_metadata.get("folder_id"))
        active_match = self.find_folder_by_name(
            destination_parent_id,
            requested_name,
            statuses={self.STATUS_ACTIVE},
            ignore_folder_id=source_folder_id,
        )
        deleted_match = self.find_folder_by_name(
            destination_parent_id,
            requested_name,
            statuses={self.STATUS_DELETED},
            ignore_folder_id=source_folder_id,
        )

        if move_mode == self.MOVE_MODE_MERGE and active_match:
            return active_match

        if active_match and is_root and move_mode == self.MOVE_MODE_AVOID_CONFLICT:
            folder_name = self.build_move_root_folder_name(destination_parent_id, requested_name)
            return self.create_folder_metadata_only(destination_parent_id, folder_name, status=self.STATUS_ACTIVE)

        if active_match:
            return active_match

        if deleted_match:
            self.revive_folder_for_move(deleted_match, requested_name)
            return self.get_folder(deleted_match["folder_id"], require_active=True)

        return self.create_folder_metadata_only(destination_parent_id, requested_name, status=self.STATUS_ACTIVE)

    def create_target_file_for_move(
        self,
        source_file_metadata,
        destination_parent_id: str | None,
        move_mode: str,
        *,
        is_root: bool,
    ):
        source_status = source_file_metadata.get("status")
        requested_name = str(source_file_metadata.get("file_name") or "").strip()
        final_name = requested_name

        if source_status == self.STATUS_ACTIVE:
            if is_root and move_mode == self.MOVE_MODE_AVOID_CONFLICT:
                final_name = self.build_move_root_file_name(destination_parent_id, requested_name)
            elif self.has_active_file_with_name(destination_parent_id, requested_name):
                final_name = self.build_moved_file_name(destination_parent_id, requested_name)

        return {
            "file_id": self.generate_short_id(),
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(destination_parent_id),
            "file_name": final_name,
            "file_extension": self.get_file_extension(final_name),
            "status": source_status,
            "created_at": self.now_iso(),
            "deleted_at": source_file_metadata.get("deleted_at") if source_status == self.STATUS_DELETED else None,
        }

    def create_folder_metadata_only(self, parent_folder_id: str | None, folder_name: str, *, status: str):
        metadata = {
            "folder_id": self.generate_short_id(),
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder_id),
            "folder_name": folder_name,
            "status": status,
            "created_at": self.now_iso(),
            "deleted_at": None if status == self.STATUS_ACTIVE else self.now_iso(),
            "children_count": 0,
        }
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_folder_key(metadata),
            Body=b"",
        )
        self.folder_table.put_item(Item=metadata)
        self.increment_children_count(parent_folder_id)
        return metadata

    def revive_folder_for_move(self, folder_metadata, folder_name: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_metadata["folder_id"]},
            UpdateExpression="SET folder_name = :folder_name, #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":folder_name": folder_name,
                ":status": self.STATUS_ACTIVE,
                ":deleted_at": None,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def copy_file_in_s3(self, source_file_metadata, target_file_metadata):
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={
                "Bucket": self.bucket,
                "Key": self.build_file_key(source_file_metadata),
            },
            Key=self.build_file_key(target_file_metadata),
        )

    def mark_file_moved(self, file_id: str, target_file_id: str, operation_id: str):
        self.file_table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="SET #status = :status, moved_target_file_id = :target_file_id, move_operation_id = :operation_id, moved_at = :moved_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_MOVED,
                ":target_file_id": target_file_id,
                ":operation_id": operation_id,
                ":moved_at": self.now_iso(),
            },
            ConditionExpression="attribute_exists(file_id)",
        )

    def mark_folder_moved(self, folder_id: str, target_folder_id: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="SET #status = :status, moved_target_folder_id = :target_folder_id, moved_at = :moved_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_MOVED,
                ":target_folder_id": target_folder_id,
                ":moved_at": self.now_iso(),
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def mark_folder_move_target(self, folder_id: str, target_folder_id: str, operation_id: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="SET move_target_folder_id = :target_folder_id, move_operation_id = :operation_id",
            ExpressionAttributeValues={
                ":target_folder_id": target_folder_id,
                ":operation_id": operation_id,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def mark_file_deletion_pending(self, file_id: str, root_id: str):
        self.file_table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="SET deletion_pending = :pending, deletion_root_id = :root_id",
            ExpressionAttributeValues={
                ":pending": True,
                ":root_id": root_id,
            },
            ConditionExpression="attribute_exists(file_id)",
        )

    def restore_file_from_deletion_pending(self, metadata):
        try:
            self.file_table.update_item(
                Key={"file_id": metadata["file_id"]},
                UpdateExpression="SET deletion_pending = :pending, deletion_root_id = :root_id, #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": False,
                    ":root_id": "",
                    ":status": self.STATUS_DELETED,
                    ":deleted_at": metadata.get("deleted_at"),
                },
                ConditionExpression="attribute_exists(file_id)",
            )
        except Exception as restore_exc:
            logger.error(
                "Failed to restore file [%s] from deletion_pending state: %s",
                metadata.get("file_id"),
                restore_exc,
            )

    def mark_folder_deletion_pending(self, folder_id: str, root_id: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="SET deletion_pending = :pending, deletion_root_id = :root_id",
            ExpressionAttributeValues={
                ":pending": True,
                ":root_id": root_id,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def reconcile_folder_children_count_chain(self, folder_id: str | None):
        current_folder_id = folder_id

        while current_folder_id:
            folder = self.get_folder_or_none(current_folder_id)
            if not folder or folder.get("user_id") != self.user_id:
                return

            child_folders, child_files = self.list_direct_children(current_folder_id)
            next_count = len(child_folders) + len(child_files)
            self.folder_table.update_item(
                Key={"folder_id": current_folder_id},
                UpdateExpression="SET children_count = :children_count",
                ExpressionAttributeValues={
                    ":children_count": next_count,
                },
                ConditionExpression="attribute_exists(folder_id)",
            )

            if folder.get("status") != self.STATUS_DELETED or next_count != 0:
                return

            parent_folder_id = self.from_storage_parent_folder_id(folder.get("parent_folder_id"))
            folder["parent_folder_id"] = parent_folder_id
            self.hard_delete_folder(folder)
            current_folder_id = parent_folder_id

    def get_active_purge_log(self):
        candidates = self.scan_current_purge_logs()
        if not candidates:
            return None

        candidates.sort(key=lambda item: ((item.get("updated_at") or ""), (item.get("created_at") or "")), reverse=True)
        return candidates[0]

    def get_active_move_log(self):
        candidates = self.scan_current_move_logs()
        if not candidates:
            return None

        candidates.sort(key=lambda item: ((item.get("updated_at") or ""), (item.get("created_at") or "")), reverse=True)
        return candidates[0]

    def scan_current_move_logs(self):
        return self.scan_table(
            self.move_log_table,
            Attr("user_id").eq(self.user_id)
            & (
                Attr("status").eq(self.LOG_STATUS_RUNNING)
                | Attr("status").eq(self.LOG_STATUS_FAILED)
            )
            & (Attr("is_resumed").not_exists() | Attr("is_resumed").eq(False))
            & Attr("operation_type").eq(self.LOG_OPERATION_MOVE),
        )

    def scan_current_purge_logs(self):
        return self.scan_table(
            self.purge_log_table,
            Attr("user_id").eq(self.user_id)
            & (
                Attr("status").eq(self.LOG_STATUS_RUNNING)
                | Attr("status").eq(self.LOG_STATUS_FAILED)
            )
            & (Attr("is_resumed").not_exists() | Attr("is_resumed").eq(False))
            & (
                Attr("operation_type").eq(self.LOG_OPERATION_PURGE)
                | Attr("operation_type").eq(self.LOG_OPERATION_DELETE)
                | Attr("operation_type").eq("dev_hard_delete")
                | Attr("operation_type").not_exists()
            ),
        )

    def create_deletion_log(
        self,
        *,
        root_id: str,
        root_kind: str,
        root_parent_id: str | None,
        trigger_operation: str,
        operation_id: str = "",
    ):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": operation_id or self.generate_short_id(),
            "user_id": self.user_id,
            "operation_type": self.LOG_OPERATION_DELETE,
            "root_entry_id": root_id,
            "root_entry_kind": root_kind,
            "root_parent_folder_id": root_parent_id or "",
            "trigger_operation": trigger_operation,
            "status": self.LOG_STATUS_RUNNING,
            "phase": "marking_pending",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "deleted_files_count": 0,
            "deleted_folders_count": 0,
        }
        self.deletion_log_table.put_item(Item=metadata)
        return metadata

    def create_replacement_log(
        self,
        *,
        old_file_id: str,
        new_file_id: str,
        file_name: str,
        preceding_log_id: str = "",
        attempt: int = 1,
        is_resumed: bool = False,
        phase: str = "marking_old_pending_delete",
    ):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": self.generate_short_id(),
            "user_id": self.user_id,
            "status": self.LOG_STATUS_RUNNING,
            "phase": phase,
            "last_error": "",
            "is_resumed": bool(is_resumed),
            "old_file_id": old_file_id,
            "new_file_id": new_file_id,
            "file_name": file_name,
            "preceding_log_id": preceding_log_id,
            "attempt": max(int(attempt or 1), 1),
            "old_file_delete_status": "",
            "old_file_marked_at": "",
            "old_file_deleted_at": "",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
        }
        self.replacement_table.put_item(Item=metadata)
        return metadata

    def create_folder_upload_log(
        self,
        *,
        root_folder_id: str,
        root_folder_name: str,
        root_parent_id: str | None,
        total_files: int,
        total_bytes: int,
    ):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": self.generate_short_id(),
            "user_id": self.user_id,
            "status": self.LOG_STATUS_RUNNING,
            "phase": "ready_to_upload",
            "last_error": "",
            "root_folder_id": root_folder_id,
            "root_parent_folder_id": root_parent_id or "",
            "root_folder_name": root_folder_name,
            "total_files": max(int(total_files or 0), 0),
            "total_bytes": max(int(total_bytes or 0), 0),
            "uploaded_files": 0,
            "uploaded_bytes": 0,
            "current_path": "",
            "last_uploaded_file_path": "",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
        }
        self.folder_upload_log_table.put_item(Item=metadata)
        return metadata

    def create_purge_log(
        self,
        *,
        operation_id: str,
        root_id: str,
        root_kind: str,
        root_parent_id: str | None,
        preceding_log_id: str,
        trigger_operation: str,
    ):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": operation_id,
            "user_id": self.user_id,
            "operation_type": self.LOG_OPERATION_PURGE,
            "attempt_type": self.LOG_ATTEMPT_INITIAL,
            "is_resumed": False,
            "preceding_log_id": preceding_log_id,
            "trigger_operation": trigger_operation,
            "root_entry_id": root_id,
            "root_entry_kind": root_kind,
            "root_folder_id": root_id if root_kind == "folder" else "",
            "root_parent_folder_id": root_parent_id or "",
            "status": self.LOG_STATUS_RUNNING,
            "phase": "deleting",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "deleted_files_count": 0,
            "deleted_folders_count": 0,
            "batch_limit": self.operation_batch_limit,
            "processed_this_run": 0,
        }
        self.purge_log_table.put_item(Item=metadata)
        return metadata

    def create_move_log(
        self,
        *,
        operation_id: str,
        source_id: str,
        source_kind: str,
        source_parent_id: str | None,
        destination_folder_id: str | None,
        mode: str,
    ):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": operation_id,
            "user_id": self.user_id,
            "operation_type": self.LOG_OPERATION_MOVE,
            "attempt_type": self.LOG_ATTEMPT_INITIAL,
            "is_resumed": False,
            "preceding_log_id": "",
            "source_entry_id": source_id,
            "source_entry_kind": source_kind,
            "destination_folder_id": destination_folder_id or "",
            "move_mode": mode,
            "status": self.LOG_STATUS_RUNNING,
            "phase": "copying",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "moved_files_count": 0,
            "moved_folders_count": 0,
            "purged_files_count": 0,
            "purged_folders_count": 0,
            "batch_limit": self.operation_batch_limit,
            "processed_this_run": 0,
        }
        self.move_log_table.put_item(Item=metadata)
        return metadata

    def create_resume_move_log(self, previous_log):
        self.mark_log_resumed(previous_log["log_id"])
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": str(previous_log.get("operation_id") or "").strip() or self.generate_short_id(),
            "user_id": self.user_id,
            "operation_type": self.LOG_OPERATION_MOVE,
            "attempt_type": self.LOG_ATTEMPT_RESUME,
            "is_resumed": False,
            "preceding_log_id": str(previous_log.get("log_id") or "").strip(),
            "source_entry_id": str(previous_log.get("source_entry_id") or "").strip(),
            "source_entry_kind": str(previous_log.get("source_entry_kind") or "").strip(),
            "destination_folder_id": str(previous_log.get("destination_folder_id") or "").strip(),
            "move_mode": str(previous_log.get("move_mode") or self.MOVE_MODE_MERGE),
            "status": self.LOG_STATUS_RUNNING,
            "phase": str(previous_log.get("phase") or "copying").strip() or "copying",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "moved_files_count": int(previous_log.get("moved_files_count") or 0),
            "moved_folders_count": int(previous_log.get("moved_folders_count") or 0),
            "purged_files_count": int(previous_log.get("purged_files_count") or 0),
            "purged_folders_count": int(previous_log.get("purged_folders_count") or 0),
            "batch_limit": self.get_log_batch_limit(previous_log),
            "processed_this_run": 0,
        }
        self.move_log_table.put_item(Item=metadata)
        return metadata

    def create_resume_purge_log(self, previous_log):
        self.mark_log_resumed(previous_log["log_id"])
        now = self.now_iso()
        root_id = self.get_log_root_id(previous_log)
        root_kind = self.get_log_root_kind(previous_log)
        metadata = {
            "log_id": self.generate_short_id(),
            "operation_id": str(previous_log.get("operation_id") or "").strip() or self.generate_short_id(),
            "user_id": self.user_id,
            "operation_type": self.LOG_OPERATION_PURGE,
            "attempt_type": self.LOG_ATTEMPT_RESUME,
            "is_resumed": False,
            "preceding_log_id": str(previous_log.get("log_id") or "").strip(),
            "trigger_operation": str(previous_log.get("trigger_operation") or "").strip(),
            "root_entry_id": root_id,
            "root_entry_kind": root_kind,
            "root_folder_id": root_id if root_kind == "folder" else "",
            "root_parent_folder_id": str(previous_log.get("root_parent_folder_id") or "").strip(),
            "status": self.LOG_STATUS_RUNNING,
            "phase": str(previous_log.get("phase") or "deleting").strip() or "deleting",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "deleted_files_count": int(previous_log.get("deleted_files_count") or 0),
            "deleted_folders_count": int(previous_log.get("deleted_folders_count") or 0),
            "batch_limit": self.get_log_batch_limit(previous_log),
            "processed_this_run": 0,
        }
        self.purge_log_table.put_item(Item=metadata)
        return metadata

    def update_deletion_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        last_error: str | None = None,
        deleted_files_count: int | None = None,
        deleted_folders_count: int | None = None,
        completed_at: str | None = None,
    ):
        update_parts = ["updated_at = :updated_at"]
        expression_attribute_names = {}
        expression_attribute_values = {
            ":updated_at": self.now_iso(),
        }

        if status is not None:
            update_parts.append("#status = :status")
            expression_attribute_names["#status"] = "status"
            expression_attribute_values[":status"] = status
        if phase is not None:
            update_parts.append("phase = :phase")
            expression_attribute_values[":phase"] = phase
        if last_error is not None:
            update_parts.append("last_error = :last_error")
            expression_attribute_values[":last_error"] = last_error
        if deleted_files_count is not None:
            update_parts.append("deleted_files_count = :deleted_files_count")
            expression_attribute_values[":deleted_files_count"] = int(deleted_files_count)
        if deleted_folders_count is not None:
            update_parts.append("deleted_folders_count = :deleted_folders_count")
            expression_attribute_values[":deleted_folders_count"] = int(deleted_folders_count)
        if completed_at is not None:
            update_parts.append("completed_at = :completed_at")
            expression_attribute_values[":completed_at"] = completed_at

        update_kwargs = {
            "Key": {"log_id": log_id},
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": expression_attribute_values,
            "ConditionExpression": "attribute_exists(log_id)",
        }
        if expression_attribute_names:
            update_kwargs["ExpressionAttributeNames"] = expression_attribute_names

        self.deletion_log_table.update_item(**update_kwargs)

    def update_replacement_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        last_error: str | None = None,
        new_file_id: str | None = None,
        preceding_log_id: str | None = None,
        attempt: int | None = None,
        is_resumed: bool | None = None,
        old_file_delete_status: str | None = None,
        old_file_marked_at: str | None = None,
        old_file_deleted_at: str | None = None,
        completed_at: str | None = None,
    ):
        update_parts = ["updated_at = :updated_at"]
        expression_attribute_names = {}
        expression_attribute_values = {
            ":updated_at": self.now_iso(),
        }

        if status is not None:
            update_parts.append("#status = :status")
            expression_attribute_names["#status"] = "status"
            expression_attribute_values[":status"] = status
        if phase is not None:
            update_parts.append("phase = :phase")
            expression_attribute_values[":phase"] = phase
        if last_error is not None:
            update_parts.append("last_error = :last_error")
            expression_attribute_values[":last_error"] = last_error
        if new_file_id is not None:
            update_parts.append("new_file_id = :new_file_id")
            expression_attribute_values[":new_file_id"] = new_file_id
        if preceding_log_id is not None:
            update_parts.append("preceding_log_id = :preceding_log_id")
            expression_attribute_values[":preceding_log_id"] = preceding_log_id
        if attempt is not None:
            update_parts.append("attempt = :attempt")
            expression_attribute_values[":attempt"] = max(int(attempt or 1), 1)
        if is_resumed is not None:
            update_parts.append("is_resumed = :is_resumed")
            expression_attribute_values[":is_resumed"] = bool(is_resumed)
        if old_file_delete_status is not None:
            update_parts.append("old_file_delete_status = :old_file_delete_status")
            expression_attribute_values[":old_file_delete_status"] = old_file_delete_status
        if old_file_marked_at is not None:
            update_parts.append("old_file_marked_at = :old_file_marked_at")
            expression_attribute_values[":old_file_marked_at"] = old_file_marked_at
        if old_file_deleted_at is not None:
            update_parts.append("old_file_deleted_at = :old_file_deleted_at")
            expression_attribute_values[":old_file_deleted_at"] = old_file_deleted_at
        if completed_at is not None:
            update_parts.append("completed_at = :completed_at")
            expression_attribute_values[":completed_at"] = completed_at

        update_kwargs = {
            "Key": {"log_id": log_id},
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": expression_attribute_values,
            "ConditionExpression": "attribute_exists(log_id)",
        }
        if expression_attribute_names:
            update_kwargs["ExpressionAttributeNames"] = expression_attribute_names

        self.replacement_table.update_item(**update_kwargs)

    def update_folder_upload_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        last_error: str | None = None,
        uploaded_files: int | None = None,
        uploaded_bytes: int | None = None,
        current_path: str | None = None,
        last_uploaded_file_path: str | None = None,
        completed_at: str | None = None,
    ):
        update_parts = ["updated_at = :updated_at"]
        expression_attribute_names = {}
        expression_attribute_values = {
            ":updated_at": self.now_iso(),
        }

        if status is not None:
            update_parts.append("#status = :status")
            expression_attribute_names["#status"] = "status"
            expression_attribute_values[":status"] = status
        if phase is not None:
            update_parts.append("phase = :phase")
            expression_attribute_values[":phase"] = phase
        if last_error is not None:
            update_parts.append("last_error = :last_error")
            expression_attribute_values[":last_error"] = last_error
        if uploaded_files is not None:
            update_parts.append("uploaded_files = :uploaded_files")
            expression_attribute_values[":uploaded_files"] = max(int(uploaded_files or 0), 0)
        if uploaded_bytes is not None:
            update_parts.append("uploaded_bytes = :uploaded_bytes")
            expression_attribute_values[":uploaded_bytes"] = max(int(uploaded_bytes or 0), 0)
        if current_path is not None:
            update_parts.append("current_path = :current_path")
            expression_attribute_values[":current_path"] = current_path
        if last_uploaded_file_path is not None:
            update_parts.append("last_uploaded_file_path = :last_uploaded_file_path")
            expression_attribute_values[":last_uploaded_file_path"] = last_uploaded_file_path
        if completed_at is not None:
            update_parts.append("completed_at = :completed_at")
            expression_attribute_values[":completed_at"] = completed_at

        update_kwargs = {
            "Key": {"log_id": log_id},
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": expression_attribute_values,
            "ConditionExpression": "attribute_exists(log_id)",
        }
        if expression_attribute_names:
            update_kwargs["ExpressionAttributeNames"] = expression_attribute_names

        self.folder_upload_log_table.update_item(**update_kwargs)

    def update_purge_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        last_error: str | None = None,
        deleted_files_count: int | None = None,
        deleted_folders_count: int | None = None,
        processed_this_run: int | None = None,
        completed_at: str | None = None,
    ):
        metadata = self.get_purge_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Purge log not found")

        if status is not None:
            metadata["status"] = status
        if phase is not None:
            metadata["phase"] = phase
        if last_error is not None:
            metadata["last_error"] = last_error
        if deleted_files_count is not None:
            metadata["deleted_files_count"] = int(deleted_files_count)
        if deleted_folders_count is not None:
            metadata["deleted_folders_count"] = int(deleted_folders_count)
        if processed_this_run is not None:
            metadata["processed_this_run"] = int(processed_this_run)
        if completed_at is not None:
            metadata["completed_at"] = completed_at

        metadata["updated_at"] = self.now_iso()
        self.purge_log_table.put_item(Item=metadata)
        return metadata

    def update_move_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        last_error: str | None = None,
        moved_files_count: int | None = None,
        moved_folders_count: int | None = None,
        purged_files_count: int | None = None,
        purged_folders_count: int | None = None,
        processed_this_run: int | None = None,
        completed_at: str | None = None,
    ):
        metadata = self.get_move_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Move log not found")

        if status is not None:
            metadata["status"] = status
        if phase is not None:
            metadata["phase"] = phase
        if last_error is not None:
            metadata["last_error"] = last_error
        if moved_files_count is not None:
            metadata["moved_files_count"] = int(moved_files_count)
        if moved_folders_count is not None:
            metadata["moved_folders_count"] = int(moved_folders_count)
        if purged_files_count is not None:
            metadata["purged_files_count"] = int(purged_files_count)
        if purged_folders_count is not None:
            metadata["purged_folders_count"] = int(purged_folders_count)
        if processed_this_run is not None:
            metadata["processed_this_run"] = int(processed_this_run)
        if completed_at is not None:
            metadata["completed_at"] = completed_at

        metadata["updated_at"] = self.now_iso()
        self.move_log_table.put_item(Item=metadata)
        return metadata

    def get_log_or_none(self, log_id: str):
        metadata = self.get_move_log_or_none(log_id)
        if metadata:
            return metadata
        return self.get_purge_log_or_none(log_id)

    def get_move_log_or_none(self, log_id: str):
        if not log_id:
            return None

        response = self.move_log_table.get_item(Key={"log_id": log_id})
        return response.get("Item")

    def get_purge_log_or_none(self, log_id: str):
        if not log_id:
            return None

        response = self.purge_log_table.get_item(Key={"log_id": log_id})
        return response.get("Item")

    def get_folder_upload_log_or_none(self, log_id: str):
        if not log_id:
            return None

        response = self.folder_upload_log_table.get_item(Key={"log_id": log_id})
        return response.get("Item")

    def start_folder_upload(self, *, root_folder_name: str, parent_id: str = "", total_files: int, total_bytes: int):
        self.resource_guard.tables(self.folder_table, self.folder_upload_log_table)
        self.resource_guard.s3_bucket(self.bucket)
        parent_folder = self.get_parent_folder(parent_id)
        normalized_name = str(root_folder_name or "").strip().strip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Root folder name is required")
        if self.has_active_folder_with_name(parent_folder["folder_id"] if parent_folder else None, normalized_name):
            raise HTTPException(status_code=400, detail="An active folder with this name already exists")

        log_id = self.generate_short_id()
        operation_id = self.generate_short_id()
        now = self.now_iso()
        root_metadata = {
            "folder_id": self.generate_short_id(),
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder["folder_id"] if parent_folder else None),
            "folder_name": normalized_name,
            "status": self.STATUS_ACTIVE,
            "created_at": now,
            "deleted_at": None,
            "children_count": 0,
            "folder_upload_operation_id": operation_id,
            "folder_upload_root_id": "",
            "upload_state": self.UPLOAD_STATE_UPLOADING,
            "upload_warning": "This folder upload is still in progress.",
        }
        root_metadata["folder_upload_root_id"] = root_metadata["folder_id"]
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_folder_key(root_metadata),
            Body=b"",
        )
        self.folder_table.put_item(
            Item=root_metadata,
            ConditionExpression="attribute_not_exists(folder_id)",
        )
        self.increment_children_count(parent_folder["folder_id"] if parent_folder else None)

        log_metadata = {
            "log_id": log_id,
            "operation_id": operation_id,
            "user_id": self.user_id,
            "status": self.LOG_STATUS_RUNNING,
            "phase": "ready_to_upload",
            "last_error": "",
            "root_folder_id": root_metadata["folder_id"],
            "root_parent_folder_id": parent_folder["folder_id"] if parent_folder else "",
            "root_folder_name": normalized_name,
            "total_files": max(int(total_files or 0), 0),
            "total_bytes": max(int(total_bytes or 0), 0),
            "uploaded_files": 0,
            "uploaded_bytes": 0,
            "current_path": "",
            "last_uploaded_file_path": "",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
        }
        self.folder_upload_log_table.put_item(Item=log_metadata)
        return {
            "log_id": log_id,
            "operation_id": operation_id,
            "root_folder_id": root_metadata["folder_id"],
            "root_folder_name": normalized_name,
        }

    def update_folder_upload_progress(
        self,
        *,
        log_id: str,
        phase: str = "",
        current_path: str = "",
        last_uploaded_file_path: str = "",
        uploaded_files: int = 0,
        uploaded_bytes: int = 0,
    ):
        self.resource_guard.tables(self.folder_upload_log_table)
        metadata = self.get_folder_upload_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Folder upload log not found")

        self.update_folder_upload_log(
            log_id,
            phase=phase or None,
            current_path=current_path or None,
            last_uploaded_file_path=last_uploaded_file_path or None,
            uploaded_files=uploaded_files,
            uploaded_bytes=uploaded_bytes,
        )
        return {"updated": log_id}

    def finalize_folder_upload(self, log_id: str):
        self.resource_guard.tables(self.folder_upload_log_table, self.folder_table, self.file_table)
        metadata = self.get_folder_upload_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Folder upload log not found")
        if str(metadata.get("status") or "").strip() == self.LOG_STATUS_DONE:
            return {
                "log_id": log_id,
                "root_folder_id": self.normalize_id(metadata.get("root_folder_id")),
                "status": self.LOG_STATUS_DONE,
            }

        operation_id = self.normalize_id(metadata.get("operation_id"))
        root_folder_id = self.normalize_id(metadata.get("root_folder_id"))
        root_folder = self.get_folder(root_folder_id, require_active=False)
        self.update_folder_upload_log(log_id, phase="finalizing")
        self.mark_folder_upload_root_state(
            root_folder_id,
            upload_state=self.UPLOAD_STATE_FINALIZING,
            upload_warning="This folder upload is being finalized.",
        )

        try:
            folders = self.scan_table(
                self.folder_table,
                Attr("user_id").eq(self.user_id) & Attr("folder_upload_operation_id").eq(operation_id),
            )
            files = self.scan_table(
                self.file_table,
                Attr("user_id").eq(self.user_id) & Attr("folder_upload_operation_id").eq(operation_id),
            )

            folders.sort(key=lambda item: ((item.get("path") or ""), (item.get("created_at") or ""), (item.get("folder_id") or "")))
            for folder in folders:
                if folder.get("folder_id") == root_folder_id:
                    continue
                self.clear_folder_upload_tracking(folder["folder_id"])
            for file_metadata in files:
                self.clear_file_upload_tracking(file_metadata["file_id"])
            self.clear_folder_upload_tracking(root_folder_id)
            self.update_folder_upload_log(
                log_id,
                status=self.LOG_STATUS_DONE,
                phase="done",
                completed_at=self.now_iso(),
                last_error="",
            )
            return {
                "log_id": log_id,
                "root_folder_id": root_folder_id,
                "status": self.LOG_STATUS_DONE,
            }
        except Exception as exc:
            root_warning = "This folder upload is incomplete and needs repair."
            self.mark_folder_upload_root_state(
                root_folder_id,
                upload_state=self.UPLOAD_STATE_REPAIR_REQUIRED,
                upload_warning=root_warning,
            )
            self.update_folder_upload_log(
                log_id,
                status=self.LOG_STATUS_REPAIR_REQUIRED,
                phase="repair_required",
                last_error=str(exc),
            )
            raise HTTPException(
                status_code=409,
                detail="Folder upload finished transferring, but finalization failed. The folder remains visible and needs repair.",
            ) from exc

    def fail_folder_upload(self, *, log_id: str, last_error: str = "", current_path: str = ""):
        self.resource_guard.tables(self.folder_upload_log_table, self.folder_table, self.file_table)
        self.resource_guard.s3_bucket(self.bucket)
        metadata = self.get_folder_upload_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Folder upload log not found")
        current_status = str(metadata.get("status") or "").strip()
        current_phase = str(metadata.get("phase") or "").strip()
        if current_status in {self.LOG_STATUS_DONE, self.LOG_STATUS_FAILED, self.LOG_STATUS_REPAIR_REQUIRED}:
            return {
                "log_id": log_id,
                "root_folder_id": self.normalize_id(metadata.get("root_folder_id")),
                "status": current_status or self.LOG_STATUS_FAILED,
            }
        if current_phase in {"finalizing", "repair_required", "done"}:
            return {
                "log_id": log_id,
                "root_folder_id": self.normalize_id(metadata.get("root_folder_id")),
                "status": current_status or self.LOG_STATUS_RUNNING,
            }

        operation_id = self.normalize_id(metadata.get("operation_id"))
        root_folder_id = self.normalize_id(metadata.get("root_folder_id"))
        root_parent_id = self.normalize_id(metadata.get("root_parent_folder_id"))
        folders = self.scan_table(
            self.folder_table,
            Attr("user_id").eq(self.user_id) & Attr("folder_upload_operation_id").eq(operation_id),
        )
        files = self.scan_table(
            self.file_table,
            Attr("user_id").eq(self.user_id) & Attr("folder_upload_operation_id").eq(operation_id),
        )

        file_delete_keys = []
        for file_metadata in files:
            try:
                file_delete_keys.append({"Key": self.build_file_key(file_metadata)})
            except Exception:
                continue
        folder_delete_keys = []
        folders_by_depth = sorted(
            folders,
            key=lambda item: self.build_object_depth(self.build_folder_key(item)),
            reverse=True,
        )
        for folder_metadata in folders_by_depth:
            try:
                folder_delete_keys.append({"Key": self.build_folder_key(folder_metadata)})
            except Exception:
                continue

        self.delete_s3_keys(file_delete_keys + folder_delete_keys)

        affected_parent_ids = {root_parent_id}
        for file_metadata in files:
            affected_parent_ids.add(self.normalize_id(self.from_storage_parent_folder_id(file_metadata.get("parent_folder_id"))))
            self.file_table.delete_item(Key={"file_id": file_metadata["file_id"]})
        for folder_metadata in folders_by_depth:
            affected_parent_ids.add(self.normalize_id(self.from_storage_parent_folder_id(folder_metadata.get("parent_folder_id"))))
            self.folder_table.delete_item(Key={"folder_id": folder_metadata["folder_id"]})

        for parent_folder_id in {item for item in affected_parent_ids if item}:
            self.reconcile_folder_children_count_chain(parent_folder_id)

        self.update_folder_upload_log(
            log_id,
            status=self.LOG_STATUS_FAILED,
            phase="failed",
            last_error=last_error or "Folder upload failed",
            current_path=current_path or None,
            completed_at=self.now_iso(),
        )
        return {
            "log_id": log_id,
            "root_folder_id": root_folder_id,
            "status": self.LOG_STATUS_FAILED,
        }

    def mark_log_resumed(self, log_id: str):
        metadata = self.get_move_log_or_none(log_id)
        target_table = self.move_log_table
        if not metadata:
            metadata = self.get_purge_log_or_none(log_id)
            target_table = self.purge_log_table
        if not metadata or metadata.get("user_id") != self.user_id:
            return
        metadata["is_resumed"] = True
        metadata["updated_at"] = self.now_iso()
        target_table.put_item(Item=metadata)

    @staticmethod
    def get_log_root_id(metadata):
        if not metadata:
            return ""
        return str(metadata.get("root_entry_id") or metadata.get("root_folder_id") or "").strip()

    @staticmethod
    def get_log_root_kind(metadata):
        if not metadata:
            return ""
        root_kind = str(metadata.get("root_entry_kind") or "").strip()
        if root_kind:
            return root_kind
        if str(metadata.get("root_folder_id") or "").strip():
            return "folder"
        return ""

    def get_log_batch_limit(self, metadata):
        raw_value = metadata.get("batch_limit") if metadata else None
        try:
            return max(int(raw_value or self.operation_batch_limit), 1)
        except (TypeError, ValueError):
            return self.operation_batch_limit

    def resolve_original_move_root(self, log_metadata, source_kind: str, fallback_root_id: str):
        operation_id = str(log_metadata.get("operation_id") or "").strip()
        fallback_root_id = self.normalize_id(fallback_root_id)
        if not operation_id:
            return fallback_root_id, source_kind

        if source_kind == "folder":
            candidates = self.scan_table(
                self.folder_table,
                Attr("user_id").eq(self.user_id)
                & Attr("status").eq(self.STATUS_MOVED)
                & Attr("move_operation_id").eq(operation_id),
            )
            if candidates:
                candidate_ids = {str(item.get("folder_id") or "").strip() for item in candidates}
                root_candidates = [
                    item for item in candidates
                    if self.normalize_id(self.from_storage_parent_folder_id(item.get("parent_folder_id"))) not in candidate_ids
                ]
                if root_candidates:
                    root_candidates.sort(key=lambda item: ((item.get("created_at") or ""), (item.get("folder_id") or "")))
                    return str(root_candidates[0]["folder_id"]).strip(), "folder"

        if source_kind == "file":
            candidates = self.scan_table(
                self.file_table,
                Attr("user_id").eq(self.user_id)
                & Attr("status").eq(self.STATUS_MOVED)
                & Attr("move_operation_id").eq(operation_id),
            )
            if candidates:
                candidates.sort(key=lambda item: ((item.get("created_at") or ""), (item.get("file_id") or "")))
                return str(candidates[0]["file_id"]).strip(), "file"

        return fallback_root_id, source_kind

    @staticmethod
    def create_processing_budget(batch_limit: int):
        return {
            "remaining": max(int(batch_limit or 1), 1),
            "processed": 0,
            "complete": True,
        }

    @staticmethod
    def consume_processing_budget(budget: dict):
        if int(budget.get("remaining") or 0) <= 0:
            budget["complete"] = False
            return False

        budget["remaining"] = int(budget.get("remaining") or 0) - 1
        budget["processed"] = int(budget.get("processed") or 0) + 1
        return True

    def build_upload_post_conditions(self, file_type: str):
        conditions = [
            ["content-length-range", 1, self.MAX_UPLOAD_BYTES],
        ]
        normalized_file_type = str(file_type or "").strip()
        if normalized_file_type:
            conditions.append({"Content-Type": normalized_file_type})
        return conditions

    def get_upload_signing_client(self):
        if self._upload_signing_client is None:
            bucket_region = self.get_bucket_region()
            endpoint_url = None if bucket_region == "us-east-1" else f"https://s3.{bucket_region}.amazonaws.com"
            self._upload_signing_client = boto3.client(
                "s3",
                region_name=bucket_region,
                endpoint_url=endpoint_url,
                config=Config(signature_version="s3v4"),
            )
        return self._upload_signing_client

    def get_bucket_region(self):
        if self._bucket_region is not None:
            return self._bucket_region

        response = self.s3.get_bucket_location(Bucket=self.bucket)
        location = response.get("LocationConstraint")
        self._bucket_region = location or "us-east-1"
        return self._bucket_region

    def encode_upload_token(
        self,
        *,
        file_id: str,
        parent_folder_id: str | None,
        file_name: str,
        file_extension: str,
        content_type: str,
        object_key: str,
        issued_at: datetime,
        replacement_log_id: str = "",
        replacement_operation_id: str = "",
        replacing_file_id: str = "",
        folder_upload_operation_id: str = "",
        folder_upload_root_id: str = "",
    ):
        expires_at = int(issued_at.timestamp()) + self.DIRECT_UPLOAD_EXPIRES_SECONDS
        return jwt.encode(
            {
                "sub": self.user_id,
                "file_id": file_id,
                "parent_folder_id": parent_folder_id or "",
                "file_name": file_name,
                "file_extension": file_extension,
                "content_type": content_type,
                "object_key": object_key,
                "replacement_log_id": replacement_log_id,
                "replacement_operation_id": replacement_operation_id,
                "replacing_file_id": replacing_file_id,
                "folder_upload_operation_id": folder_upload_operation_id,
                "folder_upload_root_id": folder_upload_root_id,
                "exp": expires_at,
            },
            auth_settings.jwt_secret,
            algorithm=auth_settings.jwt_algorithm,
        )

    def decode_upload_token(self, upload_token: str):
        try:
            payload = jwt.decode(
                upload_token,
                auth_settings.jwt_secret,
                algorithms=[auth_settings.jwt_algorithm],
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status_code=400, detail="Upload session is invalid or expired") from exc

        if str(payload.get("sub") or "").strip() != self.user_id:
            raise HTTPException(status_code=403, detail="Upload session does not belong to the current user")

        return payload

    def restore_objects(self, file_ids: list[str]):
        self.resource_guard.tables(self.file_table, self.folder_table)
        normalized_ids = self.normalize_file_ids(file_ids)
        restored_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=False)
            if metadata.get("status") != self.STATUS_DELETED:
                raise HTTPException(status_code=400, detail="Only deleted files can be restored")

            target_parent_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
            if target_parent_id:
                self.ensure_folder_chain_active(target_parent_id)
            restored_file_name = self.build_restored_file_name(
                target_parent_id,
                metadata.get("file_name", ""),
                ignore_file_id=file_id,
            )

            self.file_table.update_item(
                Key={"file_id": file_id},
                UpdateExpression="SET file_name = :file_name, file_extension = :file_extension, #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":file_name": restored_file_name,
                    ":file_extension": self.get_file_extension(restored_file_name),
                    ":status": self.STATUS_ACTIVE,
                    ":deleted_at": None,
                },
                ConditionExpression="attribute_exists(file_id)",
            )

            restored_items.append({
                "file_id": file_id,
                "object_name": restored_file_name,
                "parent_id": target_parent_id or "",
            })

        return {
            "restored": restored_items,
        }

    def soft_delete_files(self, file_ids: list[str]):
        self.resource_guard.tables(self.file_table)
        normalized_ids = self.normalize_file_ids(file_ids)
        deleted_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=True)
            result = self.soft_delete_file(metadata)
            deleted_items.append({
                "file_id": file_id,
                "object_name": metadata.get("file_name"),
                "deleted_at": result.get("deleted_at"),
            })

        return {
            "deleted": deleted_items,
        }

    def permanently_delete_objects(self, file_ids: list[str]):
        self.resource_guard.tables(self.file_table, self.folder_table, self.deletion_log_table)
        self.resource_guard.s3_bucket(self.bucket)
        normalized_ids = self.normalize_file_ids(file_ids)
        deleted_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=False)
            if metadata.get("status") != self.STATUS_DELETED:
                raise HTTPException(status_code=400, detail="Only deleted files can be permanently deleted")
            self.hard_delete_deleted_file(metadata)
            deleted_items.append({
                "file_id": file_id,
                "object_name": metadata.get("file_name"),
            })

        return {
            "deleted": deleted_items,
        }

    def hard_delete_deleted_file(self, metadata):
        file_id = str(metadata.get("file_id") or "").strip()
        parent_folder_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
        deletion_log = self.create_deletion_log(
            root_id=file_id,
            root_kind="file",
            root_parent_id=parent_folder_id,
            trigger_operation="trash_delete",
        )

        try:
            self.mark_file_deletion_pending(file_id, file_id)
            self.update_deletion_log(
                deletion_log["log_id"],
                phase="deleting_s3",
            )
            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.build_file_key(metadata),
            )
            self.update_deletion_log(
                deletion_log["log_id"],
                phase="removing_db_entry",
            )
            self.file_table.delete_item(
                Key={"file_id": file_id},
                ConditionExpression="attribute_exists(file_id)",
            )
            self.handle_parent_after_child_hard_delete(parent_folder_id)
            self.update_deletion_log(
                deletion_log["log_id"],
                status=self.LOG_STATUS_DONE,
                phase="completed",
                deleted_files_count=1,
                completed_at=self.now_iso(),
            )
        except Exception as exc:
            self.restore_file_from_deletion_pending(metadata)
            self.update_deletion_log(
                deletion_log["log_id"],
                status=self.LOG_STATUS_FAILED,
                phase="failed",
                last_error=str(exc),
            )
            raise

    def soft_delete_file(self, metadata):
        if metadata.get("status") != self.STATUS_ACTIVE:
            raise HTTPException(status_code=400, detail="File is not active")

        deleted_at = self.now_iso()
        self.file_table.update_item(
            Key={"file_id": metadata["file_id"]},
            UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_DELETED,
                ":deleted_at": deleted_at,
            },
            ConditionExpression="attribute_exists(file_id)",
        )

        return {
            "file_id": metadata["file_id"],
            "deleted_at": deleted_at,
            "hard_deleted": False,
        }

    def soft_delete_folder(self, metadata):
        if metadata.get("status") != self.STATUS_ACTIVE:
            raise HTTPException(status_code=400, detail="Folder is not active")
        if self.has_active_children(metadata["folder_id"]):
            raise HTTPException(status_code=400, detail="Folder still has active children")

        deleted_at = self.now_iso()
        normalized_parent_folder_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
        next_metadata = dict(metadata)
        next_metadata["status"] = self.STATUS_DELETED
        next_metadata["deleted_at"] = deleted_at
        next_metadata["parent_folder_id"] = normalized_parent_folder_id
        self.folder_table.update_item(
            Key={"folder_id": metadata["folder_id"]},
            UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": self.STATUS_DELETED,
                ":deleted_at": deleted_at,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

        if int(metadata.get("children_count", 0)) == 0:
            self.hard_delete_folder(next_metadata)
            self.handle_parent_after_child_hard_delete(normalized_parent_folder_id)
            return {
                "file_id": metadata["folder_id"],
                "deleted_at": deleted_at,
                "hard_deleted": True,
            }

        return {
            "file_id": metadata["folder_id"],
            "deleted_at": deleted_at,
            "hard_deleted": False,
        }

    def get_parent_folder(self, folder_id: str):
        if not folder_id:
            return None
        return self.get_folder(folder_id, require_active=True)

    def has_active_children(self, folder_id: str):
        for _ in self.scan_table(
            self.folder_table,
            Attr("user_id").eq(self.user_id)
            & Attr("parent_folder_id").eq(folder_id)
            & Attr("status").eq("active"),
        ):
            return True

        for _ in self.scan_table(
            self.file_table,
            Attr("user_id").eq(self.user_id)
            & Attr("parent_folder_id").eq(folder_id)
            & Attr("status").eq("active"),
        ):
            return True

        return False

    def build_breadcrumbs(self, current_folder):
        breadcrumbs = [{"label": "Root", "folder_id": ""}]
        if not current_folder:
            return breadcrumbs

        chain = []
        cursor = current_folder
        while cursor:
            chain.append({
                "label": cursor["folder_name"],
                "folder_id": cursor["folder_id"],
            })
            parent_id = self.from_storage_parent_folder_id(cursor.get("parent_folder_id"))
            if not parent_id:
                break
            cursor = self.get_folder_or_none(parent_id)

        breadcrumbs.extend(reversed(chain))
        return breadcrumbs

    def build_display_prefix(self, breadcrumbs):
        labels = [item["label"] for item in breadcrumbs if item.get("folder_id")]
        if not labels:
            return "/"
        return f"/{'/'.join(labels)}/"

    def serialize_folder(self, metadata, base_display_path: str):
        return {
            "kind": "folder",
            "folder_id": metadata["folder_id"],
            "file_id": metadata["folder_id"],
            "name": metadata["folder_name"],
            "path": f"{base_display_path}{metadata['folder_name']}/",
            "size": None,
            "upload_date": metadata.get("created_at"),
            "file_extension": "",
            "upload_state": str(metadata.get("upload_state") or ""),
            "upload_warning": str(metadata.get("upload_warning") or ""),
        }

    def serialize_file(self, metadata, base_display_path: str, size=None):
        return {
            "kind": "file",
            "file_id": metadata["file_id"],
            "name": metadata["file_name"],
            "path": f"{base_display_path}{metadata['file_name']}",
            "size": size,
            "upload_date": metadata.get("created_at"),
            "file_extension": metadata.get("file_extension", ""),
            "upload_state": str(metadata.get("upload_state") or ""),
        }

    def serialize_folder_state(self, metadata):
        if not metadata:
            return None
        return {
            "folder_id": metadata.get("folder_id", ""),
            "name": metadata.get("folder_name", ""),
            "upload_state": str(metadata.get("upload_state") or ""),
            "upload_warning": str(metadata.get("upload_warning") or ""),
        }

    def serialize_trashed_file(self, metadata):
        parent_folder_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
        return {
            "kind": "file",
            "file_id": metadata["file_id"],
            "name": metadata["file_name"],
            "parent_id": parent_folder_id or "",
            "parent_path": self.build_parent_display_path(parent_folder_id),
            "deleted_at": metadata.get("deleted_at"),
            "upload_date": metadata.get("created_at"),
            "file_extension": metadata.get("file_extension", ""),
        }

    def mark_folder_upload_root_state(self, folder_id: str, *, upload_state: str, upload_warning: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="SET upload_state = :upload_state, upload_warning = :upload_warning",
            ExpressionAttributeValues={
                ":upload_state": upload_state,
                ":upload_warning": upload_warning,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def clear_folder_upload_tracking(self, folder_id: str):
        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="REMOVE folder_upload_operation_id, folder_upload_root_id, upload_state, upload_warning",
            ConditionExpression="attribute_exists(folder_id)",
        )

    def clear_file_upload_tracking(self, file_id: str):
        self.file_table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="REMOVE folder_upload_operation_id, folder_upload_root_id, upload_state",
            ConditionExpression="attribute_exists(file_id)",
        )

    def delete_s3_keys(self, delete_objects: list[dict]):
        if not delete_objects:
            return

        for index in range(0, len(delete_objects), 1000):
            batch = delete_objects[index:index + 1000]
            self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": batch, "Quiet": True},
            )

    @staticmethod
    def build_object_depth(path: str):
        return len([segment for segment in str(path or "").split("/") if segment])

    def build_file_key(self, metadata):
        segments = self.build_folder_segments(self.from_storage_parent_folder_id(metadata.get("parent_folder_id")))
        segments.append(metadata["file_id"])
        return "/".join(segments)

    def build_folder_key(self, metadata):
        segments = self.build_folder_segments(self.from_storage_parent_folder_id(metadata.get("parent_folder_id")))
        segments.append(metadata["folder_id"])
        return f"{'/'.join(segments)}/"

    def build_folder_segments(self, folder_id: str | None):
        if not folder_id:
            return []

        folder = self.get_folder_or_none(folder_id)
        if not folder:
            return []
        segments = []
        cursor = folder
        while cursor:
            segments.append(cursor["folder_id"])
            parent_id = self.from_storage_parent_folder_id(cursor.get("parent_folder_id"))
            if not parent_id:
                break
            cursor = self.get_folder_or_none(parent_id)

        return list(reversed(segments))

    def get_file(self, file_id: str, require_active: bool):
        metadata = self.get_file_or_none(file_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="File not found")
        if require_active and metadata.get("status") != self.STATUS_ACTIVE:
            raise HTTPException(status_code=400, detail="File is not active")
        return metadata

    def get_folder(self, folder_id: str, require_active: bool):
        metadata = self.get_folder_or_none(folder_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Folder not found")
        if require_active and metadata.get("status") != self.STATUS_ACTIVE:
            raise HTTPException(status_code=400, detail="Folder is not active")
        return metadata

    def get_file_or_none(self, file_id: str):
        if not file_id:
            return None

        response = self.file_table.get_item(Key={"file_id": file_id})
        return response.get("Item")

    def get_folder_or_none(self, folder_id: str):
        if not folder_id:
            return None

        response = self.folder_table.get_item(Key={"folder_id": folder_id})
        return response.get("Item")

    def scan_table(self, table, filter_expression):
        scan_kwargs = {"FilterExpression": filter_expression}
        items = []

        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        return items

    def increment_children_count(self, folder_id: str | None):
        if not folder_id:
            return

        self.folder_table.update_item(
            Key={"folder_id": folder_id},
            UpdateExpression="SET children_count = if_not_exists(children_count, :zero) + :one",
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
            },
            ConditionExpression="attribute_exists(folder_id)",
        )

    def handle_parent_after_child_hard_delete(self, folder_id: str | None):
        current_folder_id = folder_id

        while current_folder_id:
            folder = self.get_folder(current_folder_id, require_active=False)
            normalized_parent_folder_id = self.from_storage_parent_folder_id(folder.get("parent_folder_id"))
            next_count = max(int(folder.get("children_count", 0)) - 1, 0)
            self.folder_table.update_item(
                Key={"folder_id": current_folder_id},
                UpdateExpression="SET children_count = :children_count",
                ExpressionAttributeValues={
                    ":children_count": next_count,
                },
                ConditionExpression="attribute_exists(folder_id)",
            )

            if folder.get("status") != self.STATUS_DELETED or next_count != 0:
                return

            folder["parent_folder_id"] = normalized_parent_folder_id
            self.hard_delete_folder(folder)
            current_folder_id = normalized_parent_folder_id

    def hard_delete_folder(self, metadata):
        self.s3.delete_object(
            Bucket=self.bucket,
            Key=self.build_folder_key(metadata),
        )
        self.folder_table.delete_item(
            Key={"folder_id": metadata["folder_id"]},
            ConditionExpression="attribute_exists(folder_id)",
        )

    def ensure_folder_chain_active(self, folder_id: str):
        chain = []
        cursor = self.get_folder(folder_id, require_active=False)
        while cursor:
            chain.append(cursor)
            parent_id = self.from_storage_parent_folder_id(cursor.get("parent_folder_id"))
            if not parent_id:
                break
            cursor = self.get_folder(parent_id, require_active=False)

        for folder in reversed(chain):
            restored_folder_name = self.build_restored_folder_name(
                self.from_storage_parent_folder_id(folder.get("parent_folder_id")),
                folder.get("folder_name", ""),
                ignore_folder_id=folder["folder_id"],
            )
            if folder.get("status") == self.STATUS_ACTIVE:
                continue
            self.folder_table.update_item(
                Key={"folder_id": folder["folder_id"]},
                UpdateExpression="SET folder_name = :folder_name, #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":folder_name": restored_folder_name,
                    ":status": self.STATUS_ACTIVE,
                    ":deleted_at": None,
                },
                ConditionExpression="attribute_exists(folder_id)",
            )

    def build_parent_display_path(self, parent_folder_id: str | None):
        if not parent_folder_id:
            return "/"

        try:
            parent_metadata = self.get_folder(parent_folder_id, require_active=False)
        except HTTPException:
            return "/"

        breadcrumbs = self.build_breadcrumbs(parent_metadata)
        return self.build_display_prefix(breadcrumbs)

    def get_s3_object_size(self, metadata):
        try:
            response = self.s3.head_object(
                Bucket=self.bucket,
                Key=self.build_file_key(metadata),
            )
        except Exception:
            return None

        return response.get("ContentLength")

    def build_upload_file_name(self, parent_folder_id: str | None, requested_name: str):
        if not self.has_active_file_with_name(parent_folder_id, requested_name):
            return requested_name

        stem = PurePosixPath(requested_name).stem or requested_name
        suffix = self.get_file_extension(requested_name)
        candidate = f"{stem}_copy{suffix}"
        copy_number = 2

        while self.has_active_file_with_name(parent_folder_id, candidate):
            candidate = f"{stem}_copy_{copy_number}{suffix}"
            copy_number += 1

        return candidate

    def build_moved_file_name(self, parent_folder_id: str | None, requested_name: str):
        if not self.has_active_file_with_name(parent_folder_id, requested_name):
            return requested_name

        stem = PurePosixPath(requested_name).stem or requested_name
        suffix = self.get_file_extension(requested_name)
        candidate = f"{stem}_moved{suffix}"
        copy_number = 2

        while self.has_active_file_with_name(parent_folder_id, candidate):
            candidate = f"{stem}_moved_{copy_number}{suffix}"
            copy_number += 1

        return candidate

    def build_move_root_file_name(self, parent_folder_id: str | None, requested_name: str):
        if not self.has_active_file_with_name(parent_folder_id, requested_name):
            return requested_name

        stem = PurePosixPath(requested_name).stem or requested_name
        suffix = self.get_file_extension(requested_name)
        candidate = f"{stem}_move{suffix}"
        copy_number = 2

        while self.has_active_file_with_name(parent_folder_id, candidate):
            candidate = f"{stem}_move_{copy_number}{suffix}"
            copy_number += 1

        return candidate

    def build_restored_file_name(self, parent_folder_id: str | None, requested_name: str, ignore_file_id: str | None = None):
        if not self.has_active_file_with_name(parent_folder_id, requested_name, ignore_file_id=ignore_file_id):
            return requested_name

        stem = PurePosixPath(requested_name).stem or requested_name
        suffix = self.get_file_extension(requested_name)
        candidate = f"{stem}_restored{suffix}"
        copy_number = 1

        while self.has_active_file_with_name(parent_folder_id, candidate, ignore_file_id=ignore_file_id):
            candidate = f"{stem}_restored_{copy_number}{suffix}"
            copy_number += 1

        return candidate

    def build_restored_folder_name(self, parent_folder_id: str | None, requested_name: str, ignore_folder_id: str | None = None):
        if not self.has_active_folder_with_name(parent_folder_id, requested_name, ignore_folder_id=ignore_folder_id):
            return requested_name

        candidate = f"{requested_name}_restored"
        copy_number = 1

        while self.has_active_folder_with_name(parent_folder_id, candidate, ignore_folder_id=ignore_folder_id):
            candidate = f"{requested_name}_restored_{copy_number}"
            copy_number += 1

        return candidate

    def build_move_root_folder_name(self, parent_folder_id: str | None, requested_name: str):
        if not self.has_active_folder_with_name(parent_folder_id, requested_name):
            return requested_name

        candidate = f"{requested_name}_move"
        copy_number = 2

        while self.has_active_folder_with_name(parent_folder_id, candidate):
            candidate = f"{requested_name}_move_{copy_number}"
            copy_number += 1

        return candidate

    def has_active_file_with_name(self, parent_folder_id: str | None, file_name: str, ignore_file_id: str | None = None):
        items = self.query_items_by_parent(
            table=self.file_table,
            parent_folder_id=parent_folder_id,
        )
        return any(
            item.get("file_id") != ignore_file_id
            and item.get("user_id") == self.user_id
            and self.same_parent_folder(self.from_storage_parent_folder_id(item.get("parent_folder_id")), parent_folder_id)
            and item.get("file_name") == file_name
            and item.get("status") == "active"
            for item in items
        )

    def find_file_by_name(
        self,
        parent_folder_id: str | None,
        file_name: str,
        *,
        statuses: set[str],
        ignore_file_id: str | None = None,
    ):
        items = self.query_items_by_parent(
            table=self.file_table,
            parent_folder_id=parent_folder_id,
        )
        for item in sorted(items, key=lambda value: ((value.get("created_at") or ""), value.get("file_id") or "")):
            if item.get("user_id") != self.user_id:
                continue
            if item.get("file_id") == ignore_file_id:
                continue
            if not self.same_parent_folder(self.from_storage_parent_folder_id(item.get("parent_folder_id")), parent_folder_id):
                continue
            if item.get("file_name") != file_name:
                continue
            if item.get("status") not in statuses:
                continue
            return item
        return None

    def has_active_folder_with_name(self, parent_folder_id: str | None, folder_name: str, ignore_folder_id: str | None = None):
        items = self.query_items_by_parent(
            table=self.folder_table,
            parent_folder_id=parent_folder_id,
        )
        return any(
            item.get("folder_id") != ignore_folder_id
            and item.get("user_id") == self.user_id
            and self.same_parent_folder(self.from_storage_parent_folder_id(item.get("parent_folder_id")), parent_folder_id)
            and item.get("folder_name") == folder_name
            and item.get("status") == "active"
            for item in items
        )

    def find_folder_by_name(
        self,
        parent_folder_id: str | None,
        folder_name: str,
        *,
        statuses: set[str],
        ignore_folder_id: str | None = None,
    ):
        items = self.query_items_by_parent(
            table=self.folder_table,
            parent_folder_id=parent_folder_id,
        )
        for item in sorted(items, key=lambda value: ((value.get("created_at") or ""), value.get("folder_id") or "")):
            if item.get("user_id") != self.user_id:
                continue
            if item.get("folder_id") == ignore_folder_id:
                continue
            if not self.same_parent_folder(self.from_storage_parent_folder_id(item.get("parent_folder_id")), parent_folder_id):
                continue
            if item.get("folder_name") != folder_name:
                continue
            if item.get("status") not in statuses:
                continue
            return item
        return None

    def query_items_by_parent(self, table, parent_folder_id: str | None):
        if parent_folder_id is None:
            return self.scan_table(
                table,
                self.build_parent_folder_filter(None),
            )

        try:
            query_kwargs = {
                "IndexName": self.PARENT_FOLDER_INDEX,
                "KeyConditionExpression": Key("parent_folder_id").eq(self.to_storage_parent_folder_id(parent_folder_id)),
            }
            items = []
            while True:
                response = table.query(**query_kwargs)
                items.extend(response.get("Items", []))
                last_evaluated_key = response.get("LastEvaluatedKey")
                if not last_evaluated_key:
                    break
                query_kwargs["ExclusiveStartKey"] = last_evaluated_key
            return items
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"ResourceNotFoundException", "ValidationException"}:
                raise

        return self.scan_table(
            table,
            self.build_parent_folder_filter(parent_folder_id),
        )

    def get_entry_for_move(self, entry_id: str):
        file_metadata = self.get_file_or_none(entry_id)
        if file_metadata and file_metadata.get("user_id") == self.user_id:
            if file_metadata.get("status") not in {self.STATUS_ACTIVE, self.STATUS_DELETED, self.STATUS_MOVED}:
                raise HTTPException(status_code=400, detail="File cannot be moved")
            return file_metadata, "file"

        folder_metadata = self.get_folder_or_none(entry_id)
        if folder_metadata and folder_metadata.get("user_id") == self.user_id:
            if folder_metadata.get("status") not in {self.STATUS_ACTIVE, self.STATUS_DELETED, self.STATUS_MOVED}:
                raise HTTPException(status_code=400, detail="Folder cannot be moved")
            return folder_metadata, "folder"

        raise HTTPException(status_code=404, detail="Move source not found")

    def ensure_destination_is_not_inside_source(self, source_folder_id: str, destination_folder_id: str | None):
        current_folder_id = destination_folder_id
        while current_folder_id:
            if current_folder_id == source_folder_id:
                raise HTTPException(status_code=400, detail="Cannot move a folder into itself or one of its descendants")
            folder = self.get_folder(current_folder_id, require_active=False)
            current_folder_id = self.from_storage_parent_folder_id(folder.get("parent_folder_id"))

    def normalize_move_mode(self, raw_mode: str | None):
        normalized_mode = str(raw_mode or "").strip().lower()
        if normalized_mode in {"", self.MOVE_MODE_MERGE}:
            return self.MOVE_MODE_MERGE
        if normalized_mode == self.MOVE_MODE_AVOID_CONFLICT:
            return self.MOVE_MODE_AVOID_CONFLICT
        raise HTTPException(status_code=400, detail="Move mode is invalid")

    @classmethod
    def to_storage_parent_folder_id(cls, parent_folder_id: str | None):
        return parent_folder_id or cls.ROOT_PARENT_FOLDER_ID

    @classmethod
    def from_storage_parent_folder_id(cls, parent_folder_id: str | None):
        if parent_folder_id in {"", None, cls.ROOT_PARENT_FOLDER_ID}:
            return None
        return parent_folder_id

    @classmethod
    def build_parent_folder_filter(cls, parent_folder_id: str | None):
        if parent_folder_id is None:
            return (
                Attr("parent_folder_id").not_exists()
                | Attr("parent_folder_id").eq(None)
                | Attr("parent_folder_id").eq(cls.ROOT_PARENT_FOLDER_ID)
            )
        return Attr("parent_folder_id").eq(cls.to_storage_parent_folder_id(parent_folder_id))

    @staticmethod
    def same_parent_folder(left_parent_folder_id: str | None, right_parent_folder_id: str | None):
        return (left_parent_folder_id or None) == (right_parent_folder_id or None)

    @staticmethod
    def normalize_file_ids(file_ids: list[str]):
        normalized_ids = []
        for file_id in file_ids:
            normalized_file_id = str(file_id or "").strip()
            if normalized_file_id:
                normalized_ids.append(normalized_file_id)

        if not normalized_ids:
            raise HTTPException(status_code=400, detail="At least one file id is required")

        return normalized_ids

    @staticmethod
    def normalize_id(raw_value: str | None):
        return str(raw_value or "").strip()

    @staticmethod
    def build_renamed_name(raw_name: str, file_extension: str):
        normalized_name = raw_name.strip().lstrip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="New name is required")

        if file_extension and not normalized_name.endswith(file_extension):
            return f"{normalized_name}{file_extension}"

        return normalized_name

    @staticmethod
    def get_file_extension(filename: str):
        suffix = PurePosixPath(filename).suffix
        return suffix if suffix and suffix != "." else ""

    @staticmethod
    def generate_short_id():
        return secrets.token_hex(6)

    @staticmethod
    def now_iso():
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def iso_from_datetime(value: datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
