from fastapi import FastAPI, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3

from app.auth_dependencies import get_authenticated_user
from app.auth_models import UserProfile
from app.auth_router import router as auth_router
from app.config import settings
from app.services.object_storage_service import ObjectStorageService

app = FastAPI()


class RenameRequest(BaseModel):
    new_name: str


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: str = ""


class ObjectSelectionRequest(BaseModel):
    file_ids: list[str]


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


def get_storage_service(current_user: UserProfile = Depends(get_authenticated_user)):
    return ObjectStorageService(
        bucket=current_user.bucket.main_bucket,
        user_id=current_user.username,
        file_metadata_table=settings.file_metadata_table,
        folder_metadata_table=settings.folder_metadata_table,
    )


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
    folder_id: str = "",
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.list_files(folder_id)


@app.get("/api/trash")
def list_trash(
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.list_trashed_files()


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    folder_id: str = Form(""),
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.upload_file(file, folder_id)


@app.get("/api/files/{file_id}/download")
def get_download_url(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_download_url(file_id)


@app.post("/api/files/{file_id}/rename")
def rename_file(
    file_id: str,
    request: RenameRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.rename_file(file_id, request.new_name)


@app.post("/api/folders")
def create_folder(
    request: CreateFolderRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.create_folder(request.parent_id, request.name)


@app.delete("/api/files/{file_id}")
def delete_file(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.delete_object(file_id)


@app.post("/api/trash/restore")
def restore_trash(
    request: ObjectSelectionRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.restore_objects(request.file_ids)


@app.post("/api/trash/delete")
def delete_trash(
    request: ObjectSelectionRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.permanently_delete_objects(request.file_ids)


@app.delete("/api/delete")
def delete_file_alias(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.delete_object(file_id)
