import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def find_env_path():
    current_file = Path(__file__).resolve()
    candidate_paths = [
        current_file.parents[2] / ".env",
        current_file.parents[1] / ".env",
        Path.cwd() / ".env",
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
    s3_bucket: str
    cors_origins: list[str]
    backend_domain: str
    file_metadata_table: str


def build_settings():
    load_settings_env()

    s3_bucket = os.getenv("S3_BUCKET", "").strip()
    if not s3_bucket:
        raise RuntimeError("S3_BUCKET is required")

    raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    cors_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    backend_domain = os.getenv("BACKEND_DOMAIN", "http://localhost:8000").strip() or "http://localhost:8000"
    file_metadata_table = os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data"

    return Settings(
        s3_bucket=s3_bucket,
        cors_origins=cors_origins,
        backend_domain=backend_domain,
        file_metadata_table=file_metadata_table,
    )


settings = build_settings()
