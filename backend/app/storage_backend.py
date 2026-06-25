from fastapi import FastAPI, Depends
from pydantic import BaseModel
import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from app.aws_error_handling import aws_exception_handler, ResourceUnavailableError, resource_unavailable_exception_handler
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
    folder_upload_operation_id: str = ""
    folder_upload_root_id: str = ""


class ObjectSelectionRequest(BaseModel):
    file_ids: list[str]


class PurgeRequest(BaseModel):
    folder_id: str = ""


class MoveRequest(BaseModel):
    source_id: str = ""
    destination_folder_id: str = ""
    mode: str = "merge"


class UploadInitRequest(BaseModel):
    file_name: str
    file_size: int
    file_type: str = ""
    folder_id: str = ""
    replace_existing: bool = False
    folder_upload_operation_id: str = ""
    folder_upload_root_id: str = ""


class UploadCompleteRequest(BaseModel):
    upload_token: str


class FolderUploadStartRequest(BaseModel):
    root_folder_name: str
    parent_id: str = ""
    total_files: int
    total_bytes: int


class FolderUploadProgressRequest(BaseModel):
    log_id: str
    phase: str = ""
    current_path: str = ""
    last_uploaded_file_path: str = ""
    uploaded_files: int = 0
    uploaded_bytes: int = 0


class FolderUploadFinalizeRequest(BaseModel):
    log_id: str


class FolderUploadFailRequest(BaseModel):
    log_id: str
    last_error: str = ""
    current_path: str = ""


app.add_exception_handler(ClientError, aws_exception_handler)
app.add_exception_handler(BotoCoreError, aws_exception_handler)
app.add_exception_handler(NoCredentialsError, aws_exception_handler)
app.add_exception_handler(PartialCredentialsError, aws_exception_handler)
app.add_exception_handler(NoRegionError, aws_exception_handler)
app.add_exception_handler(ParamValidationError, aws_exception_handler)
app.add_exception_handler(EndpointConnectionError, aws_exception_handler)
app.add_exception_handler(ConnectTimeoutError, aws_exception_handler)
app.add_exception_handler(ReadTimeoutError, aws_exception_handler)
app.add_exception_handler(ResourceUnavailableError, resource_unavailable_exception_handler)

app.include_router(auth_router)


def get_storage_service(current_user: UserProfile = Depends(get_authenticated_user)):
    return ObjectStorageService(
        bucket=current_user.bucket.main_bucket,
        user_id=current_user.user_id,
        file_metadata_table=settings.file_metadata_table,
        folder_metadata_table=settings.folder_metadata_table,
        deletion_log_table=settings.deletion_log_table,
        replacement_table=settings.replacement_table,
        folder_upload_log_table=settings.folder_upload_log_table,
        move_log_table=settings.move_log_table,
        purge_log_table=settings.purge_log_table,
        operation_batch_limit=settings.operation_batch_limit,
    )


@app.get("/api/whoami")
def whoami(current_user: UserProfile = Depends(get_authenticated_user)):
    return boto3.client("sts", region_name=settings.aws_region).get_caller_identity()


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


@app.get("/api/tree")
def get_tree(
    root_folder_id: str = "",
    include_deleted: bool = False,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_tree(root_folder_id=root_folder_id, include_deleted=include_deleted)


@app.post("/api/upload/init")
def upload_init(
    request: UploadInitRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.start_direct_upload(
        file_name=request.file_name,
        file_size=request.file_size,
        file_type=request.file_type,
        folder_id=request.folder_id,
        replace_existing=request.replace_existing,
        folder_upload_operation_id=request.folder_upload_operation_id,
        folder_upload_root_id=request.folder_upload_root_id,
    )


@app.post("/api/upload/complete")
def upload_complete(
    request: UploadCompleteRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.complete_direct_upload(request.upload_token)


@app.get("/api/files/{file_id}/download")
def get_download_url(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_download_url(file_id)


@app.get("/api/files/{file_id}/preview")
def get_preview_url(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_preview_url(file_id)


@app.get("/api/files/{file_id}/preview-text")
def get_preview_text(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_preview_text(file_id)


@app.get("/api/files/{file_id}/preview-binary")
def get_preview_binary(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_preview_binary(file_id)


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
    return storage_service.create_folder(
        request.parent_id,
        request.name,
        folder_upload_operation_id=request.folder_upload_operation_id,
        folder_upload_root_id=request.folder_upload_root_id,
    )


@app.post("/api/folder-upload/start")
def start_folder_upload(
    request: FolderUploadStartRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.start_folder_upload(
        root_folder_name=request.root_folder_name,
        parent_id=request.parent_id,
        total_files=request.total_files,
        total_bytes=request.total_bytes,
    )


@app.post("/api/folder-upload/progress")
def update_folder_upload_progress(
    request: FolderUploadProgressRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.update_folder_upload_progress(
        log_id=request.log_id,
        phase=request.phase,
        current_path=request.current_path,
        last_uploaded_file_path=request.last_uploaded_file_path,
        uploaded_files=request.uploaded_files,
        uploaded_bytes=request.uploaded_bytes,
    )


@app.post("/api/folder-upload/finalize")
def finalize_folder_upload(
    request: FolderUploadFinalizeRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.finalize_folder_upload(request.log_id)


@app.post("/api/folder-upload/fail")
def fail_folder_upload(
    request: FolderUploadFailRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.fail_folder_upload(
        log_id=request.log_id,
        last_error=request.last_error,
        current_path=request.current_path,
    )


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


@app.post("/api/trash/soft-delete")
def soft_delete_trash(
    request: ObjectSelectionRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.soft_delete_files(request.file_ids)


@app.post("/api/trash/delete")
def delete_trash(
    request: ObjectSelectionRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.permanently_delete_objects(request.file_ids)


@app.get("/api/purge/state")
def get_purge_state(
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_purge_state()


@app.get("/api/move/state")
def get_move_state(
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.get_move_state()


@app.post("/api/purge")
def purge_folder(
    request: PurgeRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.purge_folder(request.folder_id)


@app.post("/api/move")
def move_entry(
    request: MoveRequest,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.move_entry(
        source_id=request.source_id,
        destination_folder_id=request.destination_folder_id,
        mode=request.mode,
    )


@app.delete("/api/delete")
def delete_file_alias(
    file_id: str,
    storage_service: ObjectStorageService = Depends(get_storage_service),
):
    return storage_service.delete_object(file_id)
