from fastapi import APIRouter, Depends

from app.auth_dependencies import get_authenticated_user
from app.auth_models import LoginRequest, LoginResponse, UserProfile
from app.auth_service import AuthService, get_auth_service


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.login(request.username, request.password)


@router.get("/me", response_model=UserProfile)
def get_me(current_user: UserProfile = Depends(get_authenticated_user)):
    return current_user
