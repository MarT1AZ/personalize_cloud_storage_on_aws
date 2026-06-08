from app.auth_models import BucketInfo, UserProfile


# Temporary hardcoded auth data because there is no real database yet.
# This is only for testing the login flow during early development.
TEMP_USERS = {
    "user-data": UserProfile(
        username="marz",
        bucket=BucketInfo(
            main_bucket="storage-602343785232-ap-southeast-1-an",
            trash_bucket=None,
        ),
    ),
}

TEMP_PASSWORD_HASHES = {
    "username": "marz",
    "pwd-hash": "$2b$12$MhZC6HJ52pIetTV1IIpYje9FbztlJoWVaLO.90/6RJ30eY2dllK6a",
}
