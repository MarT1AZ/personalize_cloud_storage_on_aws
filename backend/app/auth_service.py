from datetime import datetime, timedelta, timezone

import bcrypt
import boto3
from boto3.dynamodb.conditions import Key
import jwt
from fastapi import HTTPException, status

from app.auth_config import auth_settings
from app.config import settings
from app.auth_models import BucketInfo, LoginResponse, UserProfile


class AuthService:
    USERNAME_INDEX = "username_index"

    def __init__(self, dynamodb_resource=None):
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb", region_name=settings.aws_region)
        self.user_table = self.dynamodb.Table(auth_settings.user_data_table)

    def get_user_item(self, username: str):
        normalized_username = str(username or "").strip()
        if not normalized_username:
            return None

        response = self.user_table.query(
            IndexName=self.USERNAME_INDEX,
            KeyConditionExpression=Key("username").eq(normalized_username),
            Limit=1,
        )
        items = response.get("Items") or []
        return items[0] if items else None

    def get_user_data(self, username: str):
        user_item = self.get_user_item(username)
        if not user_item:
            return None

        bucket_name = str(user_item.get("bucket") or "").strip()
        if not bucket_name:
            return None

        return UserProfile(
            username=str(user_item.get("username") or "").strip(),
            bucket=BucketInfo(
                main_bucket=bucket_name,
                trash_bucket=None,
            ),
        )

    def get_password_hash(self, username: str):
        user_item = self.get_user_item(username)
        if not user_item:
            return None
        return str(user_item.get("pwd-hash") or "").strip() or None

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

def get_auth_service() -> AuthService:
    return AuthService()
