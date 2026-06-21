import logging
from dataclasses import dataclass

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


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AwsErrorResult:
    status_code: int
    ui_detail: str
    log_detail: str


def build_error_result(exc: Exception) -> AwsErrorResult:
    if isinstance(exc, NoCredentialsError):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage credentials are unavailable",
            log_detail="AWS credentials are not available",
        )

    if isinstance(exc, PartialCredentialsError):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage credentials are unavailable",
            log_detail=f"AWS credentials are incomplete: {exc}",
        )

    if isinstance(exc, NoRegionError):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage configuration is unavailable",
            log_detail="AWS region is not configured",
        )

    if isinstance(exc, ParamValidationError):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage configuration is invalid",
            log_detail=f"AWS request parameters are invalid: {exc}",
        )

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage service is unavailable",
            log_detail=f"AWS service or region endpoint is unreachable: {exc}",
        )

    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code") or "").strip()
        message = str(error.get("Message") or "").strip() or str(exc)
        message_lower = message.lower()

        if code == "ResourceNotFoundException":
            if "index" in message_lower:
                return AwsErrorResult(503, "Storage metadata is unavailable", f"DynamoDB index not found: {message}")
            if "table" in message_lower:
                return AwsErrorResult(503, "Storage metadata is unavailable", f"DynamoDB table not found: {message}")
            return AwsErrorResult(503, "Storage resource is unavailable", f"AWS resource not found: {message}")

        if code == "NoSuchBucket":
            return AwsErrorResult(503, "Storage bucket is unavailable", f"S3 bucket not found: {message}")

        if code == "NoSuchKey":
            return AwsErrorResult(503, "Storage object is unavailable", f"S3 object not found: {message}")

        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return AwsErrorResult(503, "Storage access is denied", f"AWS access denied: {message}")

        if code == "InvalidAccessKeyId":
            return AwsErrorResult(503, "Storage credentials are invalid", f"AWS access key is invalid: {message}")

        if code == "SignatureDoesNotMatch":
            return AwsErrorResult(503, "Storage credentials are invalid", f"AWS secret key or request signature is invalid: {message}")

        if code == "ExpiredToken":
            return AwsErrorResult(503, "Storage credentials are expired", f"AWS session token has expired: {message}")

        if code == "UnrecognizedClientException":
            return AwsErrorResult(503, "Storage credentials are invalid", f"AWS credentials were not recognized: {message}")

        if code == "AuthorizationHeaderMalformed":
            return AwsErrorResult(503, "Storage configuration is invalid", f"AWS region is invalid for this request: {message}")

        if code == "PermanentRedirect":
            return AwsErrorResult(503, "Storage bucket is unavailable", f"S3 bucket is in a different region: {message}")

        if code == "IllegalLocationConstraintException":
            return AwsErrorResult(503, "Storage bucket is unavailable", f"S3 bucket region does not match configured AWS region: {message}")

        if code == "ValidationException":
            if "index" in message_lower:
                return AwsErrorResult(503, "Storage metadata is unavailable", f"DynamoDB index is missing or not ready: {message}")
            if "table" in message_lower:
                return AwsErrorResult(503, "Storage metadata is unavailable", f"DynamoDB table configuration is invalid: {message}")
            if "type mismatch" in message_lower:
                return AwsErrorResult(503, "Storage metadata is invalid", f"DynamoDB key type mismatch: {message}")
            return AwsErrorResult(503, "Storage configuration is invalid", f"AWS resource configuration is invalid: {message}")

        if code in {"Throttling", "ThrottlingException", "ProvisionedThroughputExceededException"}:
            return AwsErrorResult(503, "Storage service is busy", f"AWS service is throttling requests: {message}")

        return AwsErrorResult(503, "Storage request failed", f"AWS service request failed [{code}]: {message}")

    if isinstance(exc, BotoCoreError):
        return AwsErrorResult(
            status_code=503,
            ui_detail="Storage service is unavailable",
            log_detail=f"AWS service is unavailable: {exc}",
        )

    return AwsErrorResult(
        status_code=500,
        ui_detail="Unexpected server error",
        log_detail=f"Unexpected server error: {exc}",
    )


def map_aws_exception_to_http(exc: Exception) -> HTTPException:
    result = build_error_result(exc)
    return HTTPException(status_code=result.status_code, detail=result.ui_detail)


async def aws_exception_handler(request: Request, exc: Exception):
    result = build_error_result(exc)
    logger.error("AWS error on %s %s: %s", request.method, request.url.path, result.log_detail)
    return JSONResponse(
        status_code=result.status_code,
        content={"detail": result.ui_detail},
    )
