from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3
import os

from app.services.s3_storage_service import S3StorageService

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

storage_service = S3StorageService(BUCKET)


class RenameRequest(BaseModel):
    new_name: str


class CreateFolderRequest(BaseModel):
    name: str
    prefix: str = ""


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
    return storage_service.list_files(prefix)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), prefix: str = Form("")):
    return storage_service.upload_file(file, prefix)


@app.get("/api/files/{key:path}/download")
def get_download_url(key: str):
    return storage_service.get_download_url(key)


@app.post("/api/files/{key:path}/rename")
def rename_file(key: str, request: RenameRequest):
    return storage_service.rename_file(key, request.new_name)


@app.post("/api/folders")
def create_folder(request: CreateFolderRequest):
    return storage_service.create_folder(request.prefix, request.name)


@app.delete("/api/files/{key:path}")
def delete_file(key: str):
    return storage_service.delete_key(key)


@app.delete("/api/delete")
def delete_file_alias(key: str):
    return storage_service.delete_key(key)
