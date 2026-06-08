from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3
import os
from pathlib import PurePosixPath

app = FastAPI()

BUCKET = os.environ["S3_BUCKET"]
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

s3 = boto3.client("s3")


class RenameRequest(BaseModel):
    new_name: str


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/whoami")
def whoami():
    return boto3.client("sts").get_caller_identity()


@app.get("/api/")
def health():
    return {"status": "ok"}


@app.get("/api/check_bucket")
def current_bucket():
    return {"bucket": BUCKET}


@app.get("/api/files")
def list_files(prefix: str = ""):
    normalized_prefix = prefix.strip().lstrip("/")
    if normalized_prefix and not normalized_prefix.endswith("/"):
        normalized_prefix = f"{normalized_prefix}/"

    response = s3.list_objects_v2(
        Bucket=BUCKET,
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
            "name": (PurePosixPath(obj["Key"]).name or obj["Key"]),
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


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), prefix: str = Form("")):
    normalized_prefix = prefix.strip().lstrip("/")
    if normalized_prefix and not normalized_prefix.endswith("/"):
        normalized_prefix = f"{normalized_prefix}/"

    s3.upload_fileobj(
        file.file,
        BUCKET,
        f"{normalized_prefix}{file.filename}",
        ExtraArgs={
            "ContentType": file.content_type,
        },
    )

    return {
        "uploaded": f"{normalized_prefix}{file.filename}",
    }


def delete_key(key: str):
    s3.delete_object(
        Bucket=BUCKET,
        Key=key,
    )

    return {
        "deleted": key,
    }


def build_renamed_key(key: str, new_name: str):
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


@app.get("/api/files/{key:path}/download")
def get_download_url(key: str):
    filename = PurePosixPath(key).name or "download"
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET,
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


@app.post("/api/files/{key:path}/rename")
def rename_file(key: str, request: RenameRequest):
    final_key = build_renamed_key(key, request.new_name)

    if final_key != key:
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={
                "Bucket": BUCKET,
                "Key": key,
            },
            Key=final_key,
        )
        s3.delete_object(
            Bucket=BUCKET,
            Key=key,
        )

    return {
        "source_key": key,
        "renamed_key": final_key,
    }


@app.delete("/api/files/{key:path}")
def delete_file(key: str):
    return delete_key(key)


@app.delete("/api/delete")
def delete_file_alias(key: str):
    return delete_key(key)
