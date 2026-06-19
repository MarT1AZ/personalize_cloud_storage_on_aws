from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
)
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def map_aws_exception_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, NoCredentialsError):
        return HTTPException(status_code=503, detail="AWS credentials are not available")

    if isinstance(exc, PartialCredentialsError):
        return HTTPException(status_code=503, detail="AWS credentials are incomplete")

    if isinstance(exc, NoRegionError):
        return HTTPException(status_code=503, detail="AWS region is not configured")

    if isinstance(exc, ParamValidationError):
        return HTTPException(status_code=503, detail="AWS request parameters are invalid")

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return HTTPException(status_code=503, detail="AWS service or region endpoint is unreachable")

    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code") or "").strip()
        message = str(error.get("Message") or "").strip()

        if code == "ResourceNotFoundException":
            if "index" in message.lower():
                return HTTPException(status_code=503, detail="DynamoDB index was not found")
            if "table" in message.lower():
                return HTTPException(status_code=503, detail="DynamoDB table was not found")
            return HTTPException(status_code=503, detail="Required AWS resource was not found")

        if code == "NoSuchBucket":
            return HTTPException(status_code=503, detail="S3 bucket was not found")

        if code == "NoSuchKey":
            return HTTPException(status_code=503, detail="S3 object was not found")

        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return HTTPException(status_code=503, detail="AWS access was denied")

        if code == "InvalidAccessKeyId":
            return HTTPException(status_code=503, detail="AWS access key is invalid")

        if code == "SignatureDoesNotMatch":
            return HTTPException(status_code=503, detail="AWS secret key or request signature is invalid")

        if code == "ExpiredToken":
            return HTTPException(status_code=503, detail="AWS session token has expired")

        if code == "UnrecognizedClientException":
            return HTTPException(status_code=503, detail="AWS credentials were not recognized")

        if code == "AuthorizationHeaderMalformed":
            return HTTPException(status_code=503, detail="AWS region is invalid for this request")

        if code == "PermanentRedirect":
            return HTTPException(status_code=503, detail="S3 bucket is in a different region")

        if code == "IllegalLocationConstraintException":
            return HTTPException(status_code=503, detail="S3 bucket region does not match the configured AWS region")

        if code == "ValidationException":
            message_lower = message.lower()
            if "index" in message_lower:
                return HTTPException(status_code=503, detail="DynamoDB index is missing or not ready")
            if "table" in message_lower:
                return HTTPException(status_code=503, detail="DynamoDB table configuration is invalid")
            if "type mismatch" in message_lower:
                return HTTPException(status_code=503, detail="DynamoDB key type does not match the table or index schema")
            return HTTPException(status_code=503, detail="AWS resource configuration is invalid")

        if code in {"Throttling", "ThrottlingException", "ProvisionedThroughputExceededException"}:
            return HTTPException(status_code=503, detail="AWS service is throttling requests")

        return HTTPException(status_code=503, detail="AWS service request failed")

    if isinstance(exc, BotoCoreError):
        return HTTPException(status_code=503, detail="AWS service is unavailable")

    return HTTPException(status_code=500, detail="Unexpected server error")


async def aws_exception_handler(_: Request, exc: Exception):
    http_exception = map_aws_exception_to_http(exc)
    return JSONResponse(
        status_code=http_exception.status_code,
        content={"detail": http_exception.detail},
    )
