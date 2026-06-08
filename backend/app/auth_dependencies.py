from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth_models import UserProfile
from app.auth_service import auth_service


bearer_scheme = HTTPBearer(auto_error=True)


def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> UserProfile:
    return auth_service.get_current_user(credentials.credentials)
