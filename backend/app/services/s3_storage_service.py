from pathlib import PurePosixPath

import boto3
from fastapi import HTTPException


class S3StorageService:
    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def list_files(self, prefix: str = ""):
        normalized_prefix = self.normalize_prefix(prefix)
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=normalized_prefix,
            Delimiter="/",
        )

        folders = [
            {
                "kind": "folder",
                "key": folder["Prefix"],
                "name": PurePosixPath(folder["Prefix"].rstrip("/")).name or folder["Prefix"].rstrip("/"),
                "path": f'/{folder["Prefix"].lstrip("/")}',
            }
            for folder in response.get("CommonPrefixes", [])
        ]

        files = [
            {
                "kind": "file",
                "key": obj["Key"],
                "name": PurePosixPath(obj["Key"]).name or obj["Key"],
                "path": f'/{obj["Key"].lstrip("/")}',
                "size": obj.get("Size"),
                "upload_date": obj["LastModified"].isoformat() if obj.get("LastModified") else None,
            }
            for obj in response.get("Contents", [])
            if obj["Key"] != normalized_prefix
        ]

        return {
            "prefix": normalized_prefix,
            "folders": folders,
            "files": files,
        }

    def upload_file(self, upload_file, prefix: str = ""):
        normalized_prefix = self.normalize_prefix(prefix)
        object_key = f"{normalized_prefix}{upload_file.filename}"

        self.s3.upload_fileobj(
            upload_file.file,
            self.bucket,
            object_key,
            ExtraArgs={
                "ContentType": upload_file.content_type,
            },
        )

        return {
            "uploaded": object_key,
        }

    def delete_key(self, key: str):
        if key.endswith("/"):
            response = self.s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=key,
                Delimiter="/",
            )

            direct_files = [
                item
                for item in response.get("Contents", [])
                if item["Key"] != key
            ]
            direct_folders = response.get("CommonPrefixes", [])

            if direct_files or direct_folders:
                raise HTTPException(status_code=400, detail="Folder is not empty")

        self.s3.delete_object(
            Bucket=self.bucket,
            Key=key,
        )

        return {
            "deleted": key,
        }

    def get_download_url(self, key: str):
        filename = PurePosixPath(key).name or "download"
        url = self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=60,
        )

        return {
            "key": key,
            "url": url,
            "expires_in": 60,
        }

    def rename_file(self, key: str, new_name: str):
        final_key = self.build_renamed_key(key, new_name)

        if final_key != key:
            self.s3.copy_object(
                Bucket=self.bucket,
                CopySource={
                    "Bucket": self.bucket,
                    "Key": key,
                },
                Key=final_key,
            )
            self.s3.delete_object(
                Bucket=self.bucket,
                Key=key,
            )

        return {
            "source_key": key,
            "renamed_key": final_key,
        }

    def create_folder(self, prefix: str, name: str):
        folder_key = self.build_folder_key(prefix, name)

        self.s3.put_object(
            Bucket=self.bucket,
            Key=folder_key,
            Body=b"",
        )

        return {
            "created_folder": folder_key,
        }

    def build_renamed_key(self, key: str, new_name: str):
        if key.endswith("/"):
            raise HTTPException(status_code=400, detail="Folders cannot be renamed from this view")

        normalized_name = new_name.strip().lstrip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="New name is required")

        source_path = PurePosixPath(key)
        extension = source_path.suffix
        final_name = normalized_name if not extension or normalized_name.endswith(extension) else f"{normalized_name}{extension}"

        if str(source_path.parent) == ".":
            return final_name

        return source_path.parent.joinpath(final_name).as_posix()

    def build_folder_key(self, prefix: str, name: str):
        normalized_prefix = self.normalize_prefix(prefix)
        normalized_name = name.strip().strip("/")
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Folder name is required")

        return f"{normalized_prefix}{normalized_name}/"

    @staticmethod
    def normalize_prefix(prefix: str):
        normalized_prefix = prefix.strip().lstrip("/")
        if normalized_prefix and not normalized_prefix.endswith("/"):
            normalized_prefix = f"{normalized_prefix}/"
        return normalized_prefix
