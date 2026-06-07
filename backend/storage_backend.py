from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
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
def list_files():
    response = s3.list_objects_v2(Bucket=BUCKET)

    return [
        {
            "key": obj["Key"],
            "name": (PurePosixPath(obj["Key"]).name or obj["Key"]),
            "path": f'/{obj["Key"].lstrip("/")}',
            "size": obj.get("Size"),
            "upload_date": obj["LastModified"].isoformat() if obj.get("LastModified") else None,
        }
        for obj in response.get("Contents", [])
    ]


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    s3.upload_fileobj(
        file.file,
        BUCKET,
        file.filename,
        ExtraArgs={
            "ContentType": file.content_type,
        },
    )

    return {
        "uploaded": file.filename,
    }


def delete_key(key: str):
    s3.delete_object(
        Bucket=BUCKET,
        Key=key,
    )

    return {
        "deleted": key,
    }


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


@app.delete("/api/files/{key:path}")
def delete_file(key: str):
    return delete_key(key)


@app.delete("/api/delete")
def delete_file_alias(key: str):
    return delete_key(key)
