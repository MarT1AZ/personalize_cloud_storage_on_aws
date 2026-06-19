from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth_models import UserProfile
from app.auth_service import AuthService, get_auth_service


bearer_scheme = HTTPBearer(auto_error=True)


def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserProfile:
    return auth_service.get_current_user(credentials.credentials)
