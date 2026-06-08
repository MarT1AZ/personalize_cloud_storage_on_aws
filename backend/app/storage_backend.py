from fastapi import FastAPI, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3

from app.auth_dependencies import get_authenticated_user
from app.auth_models import UserProfile
from app.auth_router import router as auth_router
from app.config import settings
from app.services.s3_storage_service import S3StorageService

app = FastAPI()


class RenameRequest(BaseModel):
    new_name: str


class CreateFolderRequest(BaseModel):
    name: str
    prefix: str = ""


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


def get_storage_service(current_user: UserProfile = Depends(get_authenticated_user)):
    return S3StorageService(current_user.bucket.main_bucket)


@app.get("/api/whoami")
def whoami(current_user: UserProfile = Depends(get_authenticated_user)):
    return boto3.client("sts").get_caller_identity()


@app.get("/api/")
def health():
    return {"status": "ok"}


@app.get("/api/check_bucket")
def current_bucket(current_user: UserProfile = Depends(get_authenticated_user)):
    return {
        "main_bucket": current_user.bucket.main_bucket,
        "trash_bucket": current_user.bucket.trash_bucket,
    }


@app.get("/api/files")
def list_files(
    prefix: str = "",
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.list_files(prefix)


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    prefix: str = Form(""),
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.upload_file(file, prefix)


@app.get("/api/files/{key:path}/download")
def get_download_url(
    key: str,
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.get_download_url(key)


@app.post("/api/files/{key:path}/rename")
def rename_file(
    key: str,
    request: RenameRequest,
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.rename_file(key, request.new_name)


@app.post("/api/folders")
def create_folder(
    request: CreateFolderRequest,
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.create_folder(request.prefix, request.name)


@app.delete("/api/files/{key:path}")
def delete_file(
    key: str,
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.delete_key(key)


@app.delete("/api/delete")
def delete_file_alias(
    key: str,
    storage_service: S3StorageService = Depends(get_storage_service),
):
    return storage_service.delete_key(key)
