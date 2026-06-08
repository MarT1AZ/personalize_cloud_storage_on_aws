from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class BucketInfo(BaseModel):
    main_bucket: str
    trash_bucket: str | None = None


class UserProfile(BaseModel):
    username: str
    bucket: BucketInfo


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserProfile
