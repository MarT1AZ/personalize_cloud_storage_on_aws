import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def find_env_path():
    current_file = Path(__file__).resolve()
    candidate_paths = [
        current_file.parents[1] / ".env",
        current_file.parents[1] / "pcs_backend_production.env",
        current_file.parents[1] / "template.env",
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            return candidate

    return None


def load_settings_env():
    env_path = find_env_path()
    if env_path is not None:
        load_dotenv(env_path, override=False)


@dataclass(frozen=True)
class Settings:
    aws_region: str
    file_metadata_table: str
    folder_metadata_table: str
    deletion_log_table: str
    replacement_table: str
    folder_upload_log_table: str
    move_log_table: str
    purge_log_table: str
    operation_batch_limit: int
    show_resource_name_on_log: bool


def parse_bool_env(name: str, default: bool) -> bool:
    raw_value = str(os.getenv(name, "")).strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


def build_settings():
    load_settings_env()

    aws_region = os.getenv("AWS_REGION", "ap-southeast-1").strip() or "ap-southeast-1"
    file_metadata_table = os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data"
    folder_metadata_table = os.getenv("FOLDER_METADATA_TABLE", "folder-meta-data").strip() or "folder-meta-data"
    deletion_log_table = os.getenv("DELETION_LOG_TABLE", "deletion_log").strip() or "deletion_log"
    replacement_table = os.getenv("REPLACEMENT_TABLE", "replacement").strip() or "replacement"
    folder_upload_log_table = os.getenv("FOLDER_UPLOAD_LOG_TABLE", "folder_upload_log").strip() or "folder_upload_log"
    move_log_table = os.getenv("MOVE_LOG_TABLE", "move_log").strip() or "move_log"
    purge_log_table = os.getenv("PURGE_LOG_TABLE", "purge_log").strip() or "purge_log"
    try:
        operation_batch_limit = max(
            int(os.getenv("OPERATION_BATCH_LIMIT", "100").strip() or "100"),
            1,
        )
    except ValueError:
        operation_batch_limit = 100
    show_resource_name_on_log = parse_bool_env("SHOW_RESOURCE_NAME_ON_LOG", True)

    return Settings(
        aws_region=aws_region,
        file_metadata_table=file_metadata_table,
        folder_metadata_table=folder_metadata_table,
        deletion_log_table=deletion_log_table,
        replacement_table=replacement_table,
        folder_upload_log_table=folder_upload_log_table,
        move_log_table=move_log_table,
        purge_log_table=purge_log_table,
        operation_batch_limit=operation_batch_limit,
        show_resource_name_on_log=show_resource_name_on_log,
    )


settings = build_settings()
