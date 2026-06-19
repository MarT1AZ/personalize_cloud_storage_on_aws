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


def load_auth_env():
    env_path = find_env_path()
    if env_path is not None:
        load_dotenv(env_path, override=False)


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    user_data_table: str


def build_auth_settings():
    load_auth_env()

    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        raise RuntimeError("JWT_SECRET is required")

    jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256").strip() or "HS256"
    access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    user_data_table = os.getenv("USER_DATA_TABLE", "user-data").strip() or "user-data"

    return AuthSettings(
        jwt_secret=jwt_secret,
        jwt_algorithm=jwt_algorithm,
        access_token_expire_minutes=access_token_expire_minutes,
        user_data_table=user_data_table,
    )


auth_settings = build_auth_settings()
