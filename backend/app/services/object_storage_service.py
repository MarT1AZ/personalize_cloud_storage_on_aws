from datetime import datetime, timezone
from pathlib import PurePosixPath
import secrets

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.client import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
import jwt

from app.auth_config import auth_settings


class ObjectStorageService:
    PARENT_FOLDER_INDEX = "parent_folder_id_index"
    ROOT_PARENT_FOLDER_ID = "__root__"
    DEV_LOG_STATUS_ACTIVE = "active"
    DEV_LOG_STATUS_ONGOING = "ongoing"
    DEV_LOG_STATUS_DONE = "done"
    MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
    DIRECT_UPLOAD_EXPIRES_SECONDS = 900

    def __init__(
        self,
        bucket: str,
        user_id: str,
        file_metadata_table: str,
        folder_metadata_table: str,
        dev_log_table: str,
        s3_client=None,
        dynamodb_resource=None,
    ):
        self.bucket = bucket
        self.user_id = user_id
        self.s3 = s3_client or boto3.client("s3")
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self.file_table = self.dynamodb.Table(file_metadata_table)
        self.folder_table = self.dynamodb.Table(folder_metadata_table)
        self.dev_log_table = self.dynamodb.Table(dev_log_table)
        self._bucket_region = None
        self._upload_signing_client = None

    def list_files(self, folder_id: str = ""):
        current_folder = self.get_parent_folder(folder_id)
        breadcrumbs = self.build_breadcrumbs(current_folder)
        base_display_path = self.build_display_prefix(breadcrumbs)
        parent_filter = self.build_parent_folder_filter(folder_id or None)

        folders = []
        for folder_metadata in self.scan_table(
            self.folder_table,
            Attr("user_id").eq(self.user_id)
            & parent_filter
            & Attr("status").eq("active"),
        ):
            folders.append(self.serialize_folder(folder_metadata, base_display_path))

        files = []
        for file_metadata in self.scan_table(
            self.file_table,
            Attr("user_id").eq(self.user_id)
            & parent_filter
            & Attr("status").eq("active"),
        ):
            size = self.get_s3_object_size(file_metadata)
            files.append(self.serialize_file(file_metadata, base_display_path, size=size))

        folders.sort(key=lambda item: (item.get("name") or "").lower())
        files.sort(key=lambda item: (item.get("name") or "").lower())

        return {
            "current_folder_id": folder_id,
            "current_path": base_display_path,
            "breadcrumbs": breadcrumbs,
            "folders": folders,
            "files": files,
        }

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

    def start_direct_upload(self, file_name: str, file_size: int, file_type: str = "", folder_id: str = ""):
        normalized_file_name = str(file_name or "").strip()
        if not normalized_file_name:
            raise HTTPException(status_code=400, detail="File name is required")

        normalized_file_size = int(file_size or 0)
        if normalized_file_size <= 0:
            raise HTTPException(status_code=400, detail="File size must be greater than 0")

        parent_folder = self.get_parent_folder(folder_id)
        parent_folder_id = parent_folder["folder_id"] if parent_folder else None
        final_file_name = self.build_upload_file_name(parent_folder_id, normalized_file_name)
        file_id = self.generate_short_id()
        now = datetime.now(timezone.utc)

        metadata = {
            "file_id": file_id,
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder_id),
            "file_name": final_file_name,
            "file_extension": self.get_file_extension(final_file_name),
            "status": "active",
            "created_at": self.iso_from_datetime(now),
            "deleted_at": None,
        }
        object_key = self.build_file_key(metadata)
        upload_token = self.encode_upload_token(
            file_id=file_id,
            parent_folder_id=parent_folder_id,
            file_name=final_file_name,
            file_extension=metadata["file_extension"],
            content_type=str(file_type or "").strip(),
            object_key=object_key,
            issued_at=now,
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

        presigned_post = self.get_upload_signing_client().generate_presigned_post(**presigned_post_kwargs)

        return {
            "file_id": file_id,
            "object_name": final_file_name,
            "upload_token": upload_token,
            "upload_url": presigned_post["url"],
            "upload_fields": presigned_post["fields"],
            "max_upload_bytes": self.MAX_UPLOAD_BYTES,
        }

    def complete_direct_upload(self, upload_token: str):
        payload = self.decode_upload_token(upload_token)
        file_id = self.normalize_id(payload.get("file_id"))
        parent_folder_id = self.normalize_id(payload.get("parent_folder_id")) or None
        file_name = str(payload.get("file_name") or "").strip()
        file_extension = str(payload.get("file_extension") or "").strip()
        content_type = str(payload.get("content_type") or "").strip()
        object_key = str(payload.get("object_key") or "").strip()

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

        metadata = {
            "file_id": file_id,
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder_id),
            "file_name": file_name,
            "file_extension": file_extension or self.get_file_extension(file_name),
            "status": "active",
            "created_at": self.now_iso(),
            "deleted_at": None,
        }

        if content_type and head_response.get("ContentType") and head_response.get("ContentType") != content_type:
            raise HTTPException(status_code=400, detail="Uploaded file content type does not match the upload session")

        try:
            self.file_table.put_item(
                Item=metadata,
                ConditionExpression="attribute_not_exists(file_id)",
            )
        except ClientError as exc:
            raise HTTPException(status_code=409, detail="Upload session has already been completed") from exc
        self.increment_children_count(parent_folder_id)

        return {
            "uploaded": file_id,
            "object_name": file_name,
        }

    def create_folder(self, parent_id: str, name: str):
        parent_folder = self.get_parent_folder(parent_id)
        normalized_name = name.strip().strip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Folder name is required")
        if self.has_active_folder_with_name(parent_folder["folder_id"] if parent_folder else None, normalized_name):
            raise HTTPException(status_code=400, detail="An active folder with this name already exists")

        folder_id = self.generate_short_id()
        now = self.now_iso()
        metadata = {
            "folder_id": folder_id,
            "user_id": self.user_id,
            "parent_folder_id": self.to_storage_parent_folder_id(parent_folder["folder_id"] if parent_folder else None),
            "folder_name": normalized_name,
            "status": "active",
            "created_at": now,
            "deleted_at": None,
            "children_count": 0,
        }

        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_folder_key(metadata),
            Body=b"",
        )
        self.folder_table.put_item(Item=metadata)
        self.increment_children_count(parent_folder["folder_id"] if parent_folder else None)

        return {
            "created_folder_id": folder_id,
            "created_folder_name": normalized_name,
        }

    def get_download_url(self, file_id: str):
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

    def rename_file(self, file_id: str, new_name: str):
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
        file_metadata = self.get_file_or_none(entry_id)
        if file_metadata and file_metadata.get("user_id") == self.user_id:
            return self.soft_delete_file(file_metadata)

        folder_metadata = self.get_folder_or_none(entry_id)
        if folder_metadata and folder_metadata.get("user_id") == self.user_id:
            return self.soft_delete_folder(folder_metadata)

        raise HTTPException(status_code=404, detail="Object not found")

    def get_dev_deletion_state(self):
        metadata = self.get_active_dev_deletion_log()
        if not metadata:
            return {
                "dev_deletion": False,
                "dev_deletion_root_id": "",
                "dev_deletion_root_parent_id": "",
                "dev_deletion_phase": "idle",
            }

        return {
            "dev_deletion": metadata.get("status") in {self.DEV_LOG_STATUS_ACTIVE, self.DEV_LOG_STATUS_ONGOING},
            "dev_deletion_root_id": str(metadata.get("root_folder_id") or "").strip(),
            "dev_deletion_root_parent_id": str(metadata.get("root_parent_folder_id") or "").strip(),
            "dev_deletion_phase": str(metadata.get("phase") or "idle").strip() or "idle",
            "dev_deletion_log_id": str(metadata.get("log_id") or "").strip(),
            "dev_deletion_status": str(metadata.get("status") or "").strip(),
        }

    def dev_hard_delete_folder(self, folder_id: str = ""):
        normalized_folder_id = self.normalize_id(folder_id)
        active_log = self.get_active_dev_deletion_log()
        active_root_id = self.normalize_id(active_log.get("root_folder_id") if active_log else "")

        if active_log:
            if normalized_folder_id and active_root_id and normalized_folder_id != active_root_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"A dev deletion is already in progress for folder '{active_root_id}'. Resume that purge first.",
                )
            if not active_root_id:
                raise HTTPException(status_code=409, detail="Dev deletion is marked active but has no root id")
            return self.resume_dev_hard_delete(active_log)

        if not normalized_folder_id:
            raise HTTPException(status_code=400, detail="Folder id is required")

        root_metadata = self.get_folder(normalized_folder_id, require_active=False)
        root_parent_id = self.from_storage_parent_folder_id(root_metadata.get("parent_folder_id"))
        log_metadata = self.create_dev_deletion_log(
            root_id=normalized_folder_id,
            root_parent_id=root_parent_id,
        )
        return self.run_dev_hard_delete(root_metadata, resumed=False, log_metadata=log_metadata)

    def resume_dev_hard_delete(self, log_metadata=None):
        log_metadata = log_metadata or self.get_active_dev_deletion_log()
        if not log_metadata:
            raise HTTPException(status_code=400, detail="No dev deletion is in progress")

        root_id = self.normalize_id(log_metadata.get("root_folder_id"))
        root_parent_id = self.normalize_id(log_metadata.get("root_parent_folder_id"))
        phase = log_metadata.get("phase") or "idle"

        if not root_id:
            raise HTTPException(status_code=409, detail="Dev deletion root id is missing")

        if phase == "awaiting_parent_cleanup":
            return self.finalize_dev_hard_delete(root_id, root_parent_id, resumed=True, log_metadata=log_metadata)

        root_metadata = self.get_folder_or_none(root_id)
        if not root_metadata or root_metadata.get("user_id") != self.user_id:
            self.update_dev_deletion_log(
                log_metadata["log_id"],
                status=self.DEV_LOG_STATUS_ONGOING,
                phase="awaiting_parent_cleanup",
            )
            return self.finalize_dev_hard_delete(
                root_id,
                root_parent_id,
                resumed=True,
                log_metadata={**log_metadata, "phase": "awaiting_parent_cleanup"},
            )

        return self.run_dev_hard_delete(root_metadata, resumed=True, log_metadata=log_metadata)

    def run_dev_hard_delete(self, root_metadata, resumed: bool, log_metadata):
        root_id = root_metadata["folder_id"]
        root_parent_id = self.from_storage_parent_folder_id(root_metadata.get("parent_folder_id"))
        summary = {
            "deleted_files": 0,
            "deleted_folders": 0,
        }

        try:
            self.update_dev_deletion_log(
                log_metadata["log_id"],
                status=self.DEV_LOG_STATUS_ONGOING,
                phase="deleting",
                last_error="",
            )
            self.dev_delete_folder_subtree(root_metadata, root_id, summary)
            self.update_dev_deletion_log(
                log_metadata["log_id"],
                status=self.DEV_LOG_STATUS_ONGOING,
                phase="awaiting_parent_cleanup",
            )
            return self.finalize_dev_hard_delete(
                root_id,
                root_parent_id,
                resumed=resumed,
                summary=summary,
                log_metadata=log_metadata,
            )
        except Exception as exc:
            self.update_dev_deletion_log(
                log_metadata["log_id"],
                status=self.DEV_LOG_STATUS_ONGOING,
                phase="deleting",
                last_error=str(exc),
            )
            raise

    def finalize_dev_hard_delete(
        self,
        root_id: str,
        root_parent_id: str | None,
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

        self.update_dev_deletion_log(
            log_metadata["log_id"],
            status=self.DEV_LOG_STATUS_DONE,
            phase="completed",
            last_error="",
            deleted_files_count=summary["deleted_files"],
            deleted_folders_count=summary["deleted_folders"],
            completed_at=self.now_iso(),
        )

        return {
            "root_folder_id": root_id,
            "resumed": resumed,
            "deleted_files": summary["deleted_files"],
            "deleted_folders": summary["deleted_folders"],
            "phase": "completed",
        }

    def dev_delete_folder_subtree(self, folder_metadata, root_id: str, summary: dict):
        normalized_parent_folder_id = self.from_storage_parent_folder_id(folder_metadata.get("parent_folder_id"))
        next_folder_metadata = dict(folder_metadata)
        next_folder_metadata["parent_folder_id"] = normalized_parent_folder_id
        self.mark_folder_deletion_pending(folder_metadata["folder_id"], root_id)

        child_folders, child_files = self.list_direct_children(folder_metadata["folder_id"])

        for child_folder in child_folders:
            self.dev_delete_folder_subtree(child_folder, root_id, summary)

        for child_file in child_files:
            self.dev_delete_file(child_file, root_id)
            summary["deleted_files"] += 1

        self.hard_delete_folder(next_folder_metadata)
        summary["deleted_folders"] += 1

    def dev_delete_file(self, metadata, root_id: str):
        self.mark_file_deletion_pending(metadata["file_id"], root_id)
        self.s3.delete_object(
            Bucket=self.bucket,
            Key=self.build_file_key(metadata),
        )
        self.file_table.delete_item(
            Key={"file_id": metadata["file_id"]},
            ConditionExpression="attribute_exists(file_id)",
        )

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

            if folder.get("status") != "deleted" or next_count != 0:
                return

            parent_folder_id = self.from_storage_parent_folder_id(folder.get("parent_folder_id"))
            folder["parent_folder_id"] = parent_folder_id
            self.hard_delete_folder(folder)
            current_folder_id = parent_folder_id

    def get_active_dev_deletion_log(self):
        candidates = self.scan_table(
            self.dev_log_table,
            Attr("user_id").eq(self.user_id)
            & (
                Attr("status").eq(self.DEV_LOG_STATUS_ACTIVE)
                | Attr("status").eq(self.DEV_LOG_STATUS_ONGOING)
            ),
        )
        if not candidates:
            return None

        candidates.sort(key=lambda item: ((item.get("updated_at") or ""), (item.get("created_at") or "")), reverse=True)
        return candidates[0]

    def create_dev_deletion_log(self, root_id: str, root_parent_id: str | None):
        now = self.now_iso()
        metadata = {
            "log_id": self.generate_short_id(),
            "user_id": self.user_id,
            "root_folder_id": root_id,
            "root_parent_folder_id": root_parent_id or "",
            "status": self.DEV_LOG_STATUS_ACTIVE,
            "phase": "active",
            "created_at": now,
            "updated_at": now,
            "completed_at": "",
            "last_error": "",
            "deleted_files_count": 0,
            "deleted_folders_count": 0,
        }
        self.dev_log_table.put_item(Item=metadata)
        return metadata

    def update_dev_deletion_log(
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
        metadata = self.get_dev_log_or_none(log_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Dev deletion log not found")

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
        if completed_at is not None:
            metadata["completed_at"] = completed_at

        metadata["updated_at"] = self.now_iso()
        self.dev_log_table.put_item(Item=metadata)
        return metadata

    def get_dev_log_or_none(self, log_id: str):
        if not log_id:
            return None

        response = self.dev_log_table.get_item(Key={"log_id": log_id})
        return response.get("Item")

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
        normalized_ids = self.normalize_file_ids(file_ids)
        restored_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=False)
            if metadata.get("status") != "deleted":
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
                    ":status": "active",
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
        normalized_ids = self.normalize_file_ids(file_ids)
        deleted_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=False)
            if metadata.get("status") != "deleted":
                raise HTTPException(status_code=400, detail="Only deleted files can be permanently deleted")

            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.build_file_key(metadata),
            )
            self.file_table.delete_item(
                Key={"file_id": file_id},
                ConditionExpression="attribute_exists(file_id)",
            )
            self.handle_parent_after_child_hard_delete(
                self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
            )
            deleted_items.append({
                "file_id": file_id,
                "object_name": metadata.get("file_name"),
            })

        return {
            "deleted": deleted_items,
        }

    def soft_delete_file(self, metadata):
        if metadata.get("status") != "active":
            raise HTTPException(status_code=400, detail="File is not active")

        deleted_at = self.now_iso()
        self.file_table.update_item(
            Key={"file_id": metadata["file_id"]},
            UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "deleted",
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
        if metadata.get("status") != "active":
            raise HTTPException(status_code=400, detail="Folder is not active")
        if self.has_active_children(metadata["folder_id"]):
            raise HTTPException(status_code=400, detail="Folder still has active children")

        deleted_at = self.now_iso()
        normalized_parent_folder_id = self.from_storage_parent_folder_id(metadata.get("parent_folder_id"))
        next_metadata = dict(metadata)
        next_metadata["status"] = "deleted"
        next_metadata["deleted_at"] = deleted_at
        next_metadata["parent_folder_id"] = normalized_parent_folder_id
        self.folder_table.update_item(
            Key={"folder_id": metadata["folder_id"]},
            UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "deleted",
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
        if require_active and metadata.get("status") != "active":
            raise HTTPException(status_code=400, detail="File is not active")
        return metadata

    def get_folder(self, folder_id: str, require_active: bool):
        metadata = self.get_folder_or_none(folder_id)
        if not metadata or metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Folder not found")
        if require_active and metadata.get("status") != "active":
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

            if folder.get("status") != "deleted" or next_count != 0:
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
            if folder.get("status") == "active":
                continue
            self.folder_table.update_item(
                Key={"folder_id": folder["folder_id"]},
                UpdateExpression="SET folder_name = :folder_name, #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":folder_name": restored_folder_name,
                    ":status": "active",
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

    def query_items_by_parent(self, table, parent_folder_id: str | None):
        if parent_folder_id is None:
            return self.scan_table(
                table,
                self.build_parent_folder_filter(None),
            )

        try:
            response = table.query(
                IndexName=self.PARENT_FOLDER_INDEX,
                KeyConditionExpression=Key("parent_folder_id").eq(self.to_storage_parent_folder_id(parent_folder_id)),
            )
            return response.get("Items", [])
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"ResourceNotFoundException", "ValidationException"}:
                raise

        return self.scan_table(
            table,
            self.build_parent_folder_filter(parent_folder_id),
        )

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
