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


def load_auth_env():
    env_path = find_env_path()
    if env_path is not None:
        load_dotenv(env_path, override=False)


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    jwt_algorithm: str
    access_token_expire_minutes: int


def build_auth_settings():
    load_auth_env()

    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        raise RuntimeError("JWT_SECRET is required")

    jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256").strip() or "HS256"
    access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    return AuthSettings(
        jwt_secret=jwt_secret,
        jwt_algorithm=jwt_algorithm,
        access_token_expire_minutes=access_token_expire_minutes,
    )


auth_settings = build_auth_settings()
