from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.auth_config import auth_settings
from app.auth_data import TEMP_PASSWORD_HASHES, TEMP_USERS
from app.auth_models import LoginResponse, UserProfile


class AuthService:
    def get_user_data(self, username: str):
        user_data = TEMP_USERS.get("user-data")
        if user_data and user_data.username == username:
            return user_data
        return None

    def get_password_hash(self, username: str):
        if TEMP_PASSWORD_HASHES.get("username") == username:
            return TEMP_PASSWORD_HASHES.get("pwd-hash")
        return None

    def verify_user_credentials(self, username: str, password: str) -> UserProfile:
        user_data = self.get_user_data(username)
        password_hash = self.get_password_hash(username)
        if not user_data or not password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        password_ok = bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
        if not password_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        return user_data

    def create_access_token(self, username: str) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=auth_settings.access_token_expire_minutes)
        payload = {
            "sub": username,
            "exp": expires_at,
        }
        return jwt.encode(
            payload,
            auth_settings.jwt_secret,
            algorithm=auth_settings.jwt_algorithm,
        )

    def decode_access_token(self, token: str) -> dict:
        try:
            return jwt.decode(
                token,
                auth_settings.jwt_secret,
                algorithms=[auth_settings.jwt_algorithm],
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

    def get_current_user(self, token: str) -> UserProfile:
        payload = self.decode_access_token(token)
        username = str(payload.get("sub", "")).strip()
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        user_data = self.get_user_data(username)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        return user_data

    def login(self, username: str, password: str) -> LoginResponse:
        user_profile = self.verify_user_credentials(username, password)
        access_token = self.create_access_token(user_profile.username)
        return LoginResponse(
            access_token=access_token,
            token_type="bearer",
            user=user_profile,
        )


auth_service = AuthService()
