from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

import boto3
from fastapi import HTTPException
from boto3.dynamodb.conditions import Attr


class ObjectStorageService:
    def __init__(self, bucket: str, user_id: str, metadata_table: str, s3_client=None, dynamodb_resource=None):
        self.bucket = bucket
        self.user_id = user_id
        self.s3 = s3_client or boto3.client("s3")
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self.table = self.dynamodb.Table(metadata_table)

    def list_files(self, folder_id: str = ""):
        current_folder = None
        current_prefix = ""

        if folder_id:
            current_folder = self.get_object(folder_id, expected_type="folder", require_active=True)
            current_prefix = self.build_object_key(current_folder)

        breadcrumbs = self.build_breadcrumbs(current_folder)
        base_display_path = self.build_display_prefix(breadcrumbs)
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=current_prefix,
            Delimiter="/",
        )

        folders = []
        files = []

        for folder_prefix in response.get("CommonPrefixes", []):
            child_prefix = folder_prefix.get("Prefix", "")
            if not child_prefix or child_prefix == current_prefix:
                continue

            child_id = self.extract_object_id(child_prefix)
            metadata = self.get_object_or_none(child_id)
            if not metadata or not self.is_visible_child(metadata, folder_id, "folder"):
                continue

            folders.append(self.serialize_object(metadata, base_display_path))

        for obj in response.get("Contents", []):
            object_key = obj.get("Key", "")
            if not object_key or object_key == current_prefix:
                continue

            object_id = self.extract_object_id(object_key)
            metadata = self.get_object_or_none(object_id)
            if not metadata or not self.is_visible_child(metadata, folder_id, "file"):
                continue

            files.append(self.serialize_object(metadata, base_display_path, size=obj.get("Size")))

        return {
            "current_folder_id": folder_id,
            "current_path": base_display_path,
            "breadcrumbs": breadcrumbs,
            "folders": folders,
            "files": files,
        }

    def list_trashed_files(self):
        trashed_items = []

        for metadata in self.scan_objects(
            filter_expression=Attr("user_id").eq(self.user_id) & Attr("status").eq("trashed"),
        ):
            if metadata.get("object_type") != "file":
                continue
            trashed_items.append(self.serialize_trashed_object(metadata))

        trashed_items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
        return {
            "files": trashed_items,
        }

    def upload_file(self, upload_file, folder_id: str = ""):
        self.get_parent_folder(folder_id)
        object_id = str(uuid4())
        now = self.now_iso()
        object_name = (upload_file.filename or "").strip()
        if not object_name:
            raise HTTPException(status_code=400, detail="File name is required")

        metadata = {
            "object_id": object_id,
            "user_id": self.user_id,
            "parent_id": folder_id or None,
            "object_name": object_name,
            "object_type": "file",
            "file_extension": self.get_file_extension(object_name),
            "status": "active",
            "created_at": now,
            "deleted_at": None,
        }
        object_key = self.build_object_key(metadata)

        self.s3.upload_fileobj(
            upload_file.file,
            self.bucket,
            object_key,
            ExtraArgs={
                "ContentType": upload_file.content_type,
            },
        )

        self.table.put_item(Item=metadata)

        return {
            "uploaded": object_id,
            "object_name": object_name,
        }

    def create_folder(self, parent_id: str, name: str):
        self.get_parent_folder(parent_id)
        normalized_name = name.strip().strip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Folder name is required")

        object_id = str(uuid4())
        now = self.now_iso()
        metadata = {
            "object_id": object_id,
            "user_id": self.user_id,
            "parent_id": parent_id or None,
            "object_name": normalized_name,
            "object_type": "folder",
            "file_extension": "",
            "status": "active",
            "created_at": now,
            "deleted_at": None,
        }

        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_object_key(metadata),
            Body=b"",
        )

        self.table.put_item(Item=metadata)

        return {
            "created_folder_id": object_id,
            "created_folder_name": normalized_name,
        }

    def get_download_url(self, object_id: str):
        metadata = self.get_object(object_id, expected_type="file", require_active=True)
        filename = metadata["object_name"] or "download"
        url = self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self.build_object_key(metadata),
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=60,
        )

        return {
            "object_id": object_id,
            "url": url,
            "expires_in": 60,
        }

    def rename_file(self, object_id: str, new_name: str):
        metadata = self.get_object(object_id, expected_type="file", require_active=True)
        final_name = self.build_renamed_name(new_name, metadata.get("file_extension", ""))

        self.table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET object_name = :object_name, file_extension = :file_extension",
            ExpressionAttributeValues={
                ":object_name": final_name,
                ":file_extension": metadata.get("file_extension", ""),
            },
            ConditionExpression="attribute_exists(object_id)",
        )

        return {
            "object_id": object_id,
            "source_name": metadata["object_name"],
            "renamed_name": final_name,
        }

    def delete_object(self, object_id: str):
        metadata = self.get_object(object_id, require_active=True)

        if metadata["object_type"] == "folder":
            if self.has_active_children(metadata["object_id"]):
                raise HTTPException(status_code=400, detail="Folder is not empty")

            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.build_object_key(metadata),
            )
            self.table.delete_item(
                Key={"object_id": object_id},
                ConditionExpression="attribute_exists(object_id)",
            )

            return {
                "object_id": object_id,
                "deleted": True,
                "hard_deleted": True,
            }

        deleted_at = self.now_iso()
        self.table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET #status = :status, deleted_at = :deleted_at",
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":status": "trashed",
                ":deleted_at": deleted_at,
            },
            ConditionExpression="attribute_exists(object_id)",
        )

        return {
            "object_id": object_id,
            "deleted_at": deleted_at,
            "hard_deleted": False,
        }

    def restore_objects(self, object_ids: list[str]):
        normalized_ids = self.normalize_object_ids(object_ids)
        restored_items = []

        for object_id in normalized_ids:
            metadata = self.get_object(object_id, expected_type="file", require_active=False)
            if metadata.get("status") != "trashed":
                raise HTTPException(status_code=400, detail="Only trashed files can be restored")

            target_parent = self.resolve_restore_parent(metadata.get("parent_id"))
            source_key = self.resolve_object_key(metadata)
            restored_metadata = dict(metadata)
            restored_metadata["parent_id"] = target_parent.get("object_id") if target_parent else None
            target_key = self.build_object_key(restored_metadata)

            if source_key != target_key:
                self.s3.copy_object(
                    Bucket=self.bucket,
                    CopySource={"Bucket": self.bucket, "Key": source_key},
                    Key=target_key,
                )
                self.s3.delete_object(
                    Bucket=self.bucket,
                    Key=source_key,
                )

            self.table.update_item(
                Key={"object_id": object_id},
                UpdateExpression="SET parent_id = :parent_id, #status = :status, deleted_at = :deleted_at",
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":parent_id": restored_metadata.get("parent_id"),
                    ":status": "active",
                    ":deleted_at": None,
                },
                ConditionExpression="attribute_exists(object_id)",
            )

            restored_items.append({
                "object_id": object_id,
                "object_name": metadata.get("object_name"),
                "parent_id": restored_metadata.get("parent_id") or "",
            })

        return {
            "restored": restored_items,
        }

    def permanently_delete_objects(self, object_ids: list[str]):
        normalized_ids = self.normalize_object_ids(object_ids)
        deleted_items = []

        for object_id in normalized_ids:
            metadata = self.get_object(object_id, expected_type="file", require_active=False)
            if metadata.get("status") != "trashed":
                raise HTTPException(status_code=400, detail="Only trashed files can be permanently deleted")

            self.s3.delete_object(
                Bucket=self.bucket,
                Key=self.resolve_object_key(metadata),
            )
            self.table.delete_item(
                Key={"object_id": object_id},
                ConditionExpression="attribute_exists(object_id)",
            )
            deleted_items.append({
                "object_id": object_id,
                "object_name": metadata.get("object_name"),
            })

        return {
            "deleted": deleted_items,
        }

    def get_parent_folder(self, folder_id: str):
        if not folder_id:
            return None
        return self.get_object(folder_id, expected_type="folder", require_active=True)

    def has_active_children(self, folder_id: str):
        folder = self.get_object(folder_id, expected_type="folder", require_active=True)
        folder_prefix = self.build_object_key(folder)
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=folder_prefix,
            Delimiter="/",
        )

        for folder_prefix_item in response.get("CommonPrefixes", []):
            child_prefix = folder_prefix_item.get("Prefix", "")
            if not child_prefix or child_prefix == folder_prefix:
                continue

            child_metadata = self.get_object_or_none(self.extract_object_id(child_prefix))
            if self.is_visible_child(child_metadata, folder_id, "folder"):
                return True

        for file_item in response.get("Contents", []):
            object_key = file_item.get("Key", "")
            if not object_key or object_key == folder_prefix:
                continue

            child_metadata = self.get_object_or_none(self.extract_object_id(object_key))
            if self.is_visible_child(child_metadata, folder_id, "file"):
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
                "label": cursor["object_name"],
                "folder_id": cursor["object_id"],
            })
            parent_id = cursor.get("parent_id")
            if not parent_id:
                break
            cursor = self.get_object(parent_id, expected_type="folder", require_active=False)

        breadcrumbs.extend(reversed(chain))
        return breadcrumbs

    def build_display_prefix(self, breadcrumbs):
        labels = [item["label"] for item in breadcrumbs if item.get("folder_id")]
        if not labels:
            return "/"
        return f"/{'/'.join(labels)}/"

    def serialize_object(self, metadata, base_display_path: str, size=None):
        object_type = metadata["object_type"]
        object_name = metadata["object_name"]
        suffix = "/" if object_type == "folder" else ""

        return {
            "kind": object_type,
            "object_id": metadata["object_id"],
            "name": object_name,
            "path": f"{base_display_path}{object_name}{suffix}",
            "size": size,
            "upload_date": metadata.get("created_at"),
            "file_extension": metadata.get("file_extension", ""),
        }

    def serialize_trashed_object(self, metadata):
        return {
            "kind": metadata["object_type"],
            "object_id": metadata["object_id"],
            "name": metadata["object_name"],
            "parent_id": metadata.get("parent_id") or "",
            "parent_path": self.build_parent_display_path(metadata.get("parent_id")),
            "deleted_at": metadata.get("deleted_at"),
            "upload_date": metadata.get("created_at"),
            "file_extension": metadata.get("file_extension", ""),
        }

    def build_object_key(self, metadata):
        parts = self.build_object_segments(metadata)
        if metadata["object_type"] == "folder":
            return f"{'/'.join(parts)}/"
        return "/".join(parts)

    def resolve_object_key(self, metadata):
        try:
            return self.build_object_key(metadata)
        except HTTPException as exc:
            if exc.status_code != 404 or metadata.get("object_type") != "file":
                raise

        object_id = metadata["object_id"]
        matched_key = self.find_s3_key_by_object_id(object_id)
        if matched_key:
            return matched_key

        raise HTTPException(status_code=404, detail="Object not found in storage")

    def build_object_segments(self, metadata):
        segments = []
        current = metadata

        while current:
            segments.append(current["object_id"])
            parent_id = current.get("parent_id")
            if not parent_id:
                break
            current = self.get_object(parent_id, expected_type="folder", require_active=False)

        return list(reversed(segments))

    def get_object(self, object_id: str, expected_type: str | None = None, require_active: bool = True):
        metadata = self.get_object_or_none(object_id)
        if not metadata:
            raise HTTPException(status_code=404, detail="Object not found")

        if metadata.get("user_id") != self.user_id:
            raise HTTPException(status_code=404, detail="Object not found")

        if expected_type and metadata.get("object_type") != expected_type:
            raise HTTPException(status_code=400, detail=f"Object is not a {expected_type}")

        if require_active and metadata.get("status") != "active":
            raise HTTPException(status_code=400, detail="Object is not active")

        return metadata

    def get_object_or_none(self, object_id: str):
        if not object_id:
            return None

        response = self.table.get_item(Key={"object_id": object_id})
        return response.get("Item")

    def scan_objects(self, filter_expression):
        scan_kwargs = {"FilterExpression": filter_expression}
        items = []

        while True:
            response = self.table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        return items

    def is_visible_child(self, metadata, parent_id: str, expected_type: str):
        if not metadata:
            return False
        if metadata.get("user_id") != self.user_id:
            return False
        if metadata.get("status") != "active":
            return False
        if metadata.get("object_type") != expected_type:
            return False
        return (metadata.get("parent_id") or "") == (parent_id or "")

    def build_renamed_name(self, raw_name: str, file_extension: str):
        normalized_name = raw_name.strip().lstrip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="New name is required")

        if file_extension and not normalized_name.endswith(file_extension):
            return f"{normalized_name}{file_extension}"

        return normalized_name

    def extract_object_id(self, s3_key: str):
        normalized_key = s3_key.rstrip("/")
        return PurePosixPath(normalized_key).name or normalized_key

    def find_s3_key_by_object_id(self, object_id: str):
        continuation_token = None
        exact_key = object_id
        nested_suffix = f"/{object_id}"

        while True:
            request = {
                "Bucket": self.bucket,
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token

            response = self.s3.list_objects_v2(**request)
            for item in response.get("Contents", []):
                key = item.get("Key", "")
                if key == exact_key or key.endswith(nested_suffix):
                    return key

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return None

    def resolve_restore_parent(self, parent_id: str | None):
        if not parent_id:
            return None

        parent_metadata = self.get_object_or_none(parent_id)
        if parent_metadata and parent_metadata.get("user_id") == self.user_id and parent_metadata.get("object_type") == "folder" and parent_metadata.get("status") == "active":
            return parent_metadata

        return self.get_or_create_restored_folder()

    def get_or_create_restored_folder(self):
        for metadata in self.scan_objects(
            filter_expression=Attr("user_id").eq(self.user_id)
            & Attr("object_type").eq("folder")
            & Attr("status").eq("active")
            & Attr("object_name").eq("restored"),
        ):
            if metadata.get("parent_id") not in (None, ""):
                continue
            return metadata

        object_id = str(uuid4())
        now = self.now_iso()
        metadata = {
            "object_id": object_id,
            "user_id": self.user_id,
            "parent_id": None,
            "object_name": "restored",
            "object_type": "folder",
            "file_extension": "",
            "status": "active",
            "created_at": now,
            "deleted_at": None,
        }

        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.build_object_key(metadata),
            Body=b"",
        )
        self.table.put_item(Item=metadata)
        return metadata

    def build_parent_display_path(self, parent_id: str | None):
        if not parent_id:
            return "/"

        parent_metadata = self.get_object_or_none(parent_id)
        if not parent_metadata or parent_metadata.get("user_id") != self.user_id or parent_metadata.get("object_type") != "folder":
            return "/restored/"

        breadcrumbs = self.build_breadcrumbs(parent_metadata)
        return self.build_display_prefix(breadcrumbs)

    @staticmethod
    def normalize_object_ids(object_ids: list[str]):
        normalized_ids = []
        for object_id in object_ids:
            normalized_object_id = str(object_id or "").strip()
            if normalized_object_id:
                normalized_ids.append(normalized_object_id)

        if not normalized_ids:
            raise HTTPException(status_code=400, detail="At least one object id is required")

        return normalized_ids

    @staticmethod
    def get_file_extension(filename: str):
        suffix = PurePosixPath(filename).suffix
        return suffix if suffix and suffix != "." else ""

    @staticmethod
    def now_iso():
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
