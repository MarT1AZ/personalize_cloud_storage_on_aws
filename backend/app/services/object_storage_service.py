from datetime import datetime, timezone
from pathlib import PurePosixPath
import secrets

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import HTTPException


class ObjectStorageService:
    FILE_NAME_INDEX = "file_name_index"
    FOLDER_NAME_INDEX = "folder_name_index"

    def __init__(
        self,
        bucket: str,
        user_id: str,
        file_metadata_table: str,
        folder_metadata_table: str,
        s3_client=None,
        dynamodb_resource=None,
    ):
        self.bucket = bucket
        self.user_id = user_id
        self.s3 = s3_client or boto3.client("s3")
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self.file_table = self.dynamodb.Table(file_metadata_table)
        self.folder_table = self.dynamodb.Table(folder_metadata_table)

    def list_files(self, folder_id: str = ""):
        current_folder = self.get_parent_folder(folder_id)
        breadcrumbs = self.build_breadcrumbs(current_folder)
        base_display_path = self.build_display_prefix(breadcrumbs)

        folders = []
        for folder_metadata in self.scan_table(
            self.folder_table,
            Attr("user_id").eq(self.user_id)
            & Attr("parent_folder_id").eq(folder_id or None)
            & Attr("status").eq("active"),
        ):
            folders.append(self.serialize_folder(folder_metadata, base_display_path))

        files = []
        for file_metadata in self.scan_table(
            self.file_table,
            Attr("user_id").eq(self.user_id)
            & Attr("parent_folder_id").eq(folder_id or None)
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

    def upload_file(self, upload_file, folder_id: str = ""):
        parent_folder = self.get_parent_folder(folder_id)
        file_id = self.generate_short_id()
        now = self.now_iso()
        file_name = (upload_file.filename or "").strip()
        if not file_name:
            raise HTTPException(status_code=400, detail="File name is required")
        file_name = self.build_upload_file_name(
            parent_folder["folder_id"] if parent_folder else None,
            file_name,
        )

        metadata = {
            "file_id": file_id,
            "user_id": self.user_id,
            "parent_folder_id": parent_folder["folder_id"] if parent_folder else None,
            "file_name": file_name,
            "file_extension": self.get_file_extension(file_name),
            "status": "active",
            "created_at": now,
            "deleted_at": None,
        }

        self.s3.upload_fileobj(
            upload_file.file,
            self.bucket,
            self.build_file_key(metadata),
            ExtraArgs={"ContentType": upload_file.content_type},
        )
        self.file_table.put_item(Item=metadata)
        self.increment_children_count(metadata.get("parent_folder_id"))

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
            "parent_folder_id": parent_folder["folder_id"] if parent_folder else None,
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
        self.increment_children_count(metadata.get("parent_folder_id"))

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

    def restore_objects(self, file_ids: list[str]):
        normalized_ids = self.normalize_file_ids(file_ids)
        restored_items = []

        for file_id in normalized_ids:
            metadata = self.get_file(file_id, require_active=False)
            if metadata.get("status") != "deleted":
                raise HTTPException(status_code=400, detail="Only deleted files can be restored")

            target_parent_id = metadata.get("parent_folder_id")
            if target_parent_id:
                self.ensure_folder_chain_active(target_parent_id)

            self.file_table.update_item(
                Key={"file_id": file_id},
                UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "active",
                    ":deleted_at": None,
                },
                ConditionExpression="attribute_exists(file_id)",
            )

            restored_items.append({
                "file_id": file_id,
                "object_name": metadata.get("file_name"),
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
            self.handle_parent_after_child_hard_delete(metadata.get("parent_folder_id"))
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
            parent_id = cursor.get("parent_folder_id")
            if not parent_id:
                break
            cursor = self.get_folder(parent_id, require_active=False)

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
        return {
            "kind": "file",
            "file_id": metadata["file_id"],
            "name": metadata["file_name"],
            "parent_id": metadata.get("parent_folder_id") or "",
            "parent_path": self.build_parent_display_path(metadata.get("parent_folder_id")),
            "deleted_at": metadata.get("deleted_at"),
            "upload_date": metadata.get("created_at"),
            "file_extension": metadata.get("file_extension", ""),
        }

    def build_file_key(self, metadata):
        segments = self.build_folder_segments(metadata.get("parent_folder_id"))
        segments.append(metadata["file_id"])
        return "/".join(segments)

    def build_folder_key(self, metadata):
        segments = self.build_folder_segments(metadata.get("parent_folder_id"))
        segments.append(metadata["folder_id"])
        return f"{'/'.join(segments)}/"

    def build_folder_segments(self, folder_id: str | None):
        if not folder_id:
            return []

        folder = self.get_folder(folder_id, require_active=False)
        segments = []
        cursor = folder
        while cursor:
            segments.append(cursor["folder_id"])
            parent_id = cursor.get("parent_folder_id")
            if not parent_id:
                break
            cursor = self.get_folder(parent_id, require_active=False)

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

            parent_folder_id = folder.get("parent_folder_id")
            self.hard_delete_folder(folder)
            current_folder_id = parent_folder_id

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
            parent_id = cursor.get("parent_folder_id")
            if not parent_id:
                break
            cursor = self.get_folder(parent_id, require_active=False)

        for folder in reversed(chain):
            if folder.get("status") == "active":
                continue
            self.folder_table.update_item(
                Key={"folder_id": folder["folder_id"]},
                UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
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

    def has_active_file_with_name(self, parent_folder_id: str | None, file_name: str):
        items = self.query_items_by_name(
            table=self.file_table,
            index_name=self.FILE_NAME_INDEX,
            name_key="file_name",
            name_value=file_name,
        )
        return any(
            item.get("user_id") == self.user_id
            and self.same_parent_folder(item.get("parent_folder_id"), parent_folder_id)
            and item.get("status") == "active"
            for item in items
        )

    def has_active_folder_with_name(self, parent_folder_id: str | None, folder_name: str):
        items = self.query_items_by_name(
            table=self.folder_table,
            index_name=self.FOLDER_NAME_INDEX,
            name_key="folder_name",
            name_value=folder_name,
        )
        return any(
            item.get("user_id") == self.user_id
            and self.same_parent_folder(item.get("parent_folder_id"), parent_folder_id)
            and item.get("status") == "active"
            for item in items
        )

    def query_items_by_name(self, table, index_name: str, name_key: str, name_value: str):
        try:
            response = table.query(
                IndexName=index_name,
                KeyConditionExpression=Key(name_key).eq(name_value),
            )
            return response.get("Items", [])
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"ResourceNotFoundException", "ValidationException"}:
                raise

        return self.scan_table(
            table,
            Attr(name_key).eq(name_value),
        )

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
