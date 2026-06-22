from botocore.exceptions import ClientError

from app.aws_error_handling import ResourceUnavailableError
from app.config import settings


class ResourceGuard:
    def __init__(self, *, s3_client):
        self.s3 = s3_client
        self._bucket_checked = set()
        self._table_checked = set()

    def s3_bucket(self, bucket_name: str):
        normalized_bucket = str(bucket_name or "").strip()
        if not normalized_bucket or normalized_bucket in self._bucket_checked:
            return
        try:
            self.s3.head_bucket(Bucket=normalized_bucket)
        except ClientError as exc:
            raise ResourceUnavailableError(
                status_code=503,
                ui_detail=self._resource_message(
                    resource_kind="S3 bucket",
                    resource_name=normalized_bucket,
                    unavailable=True,
                ),
                log_detail=f"{self._resource_message(resource_kind='S3 bucket', resource_name=normalized_bucket, unavailable=False)} precheck failed: {self._format_client_error(exc)}",
            ) from exc
        self._bucket_checked.add(normalized_bucket)

    def tables(self, *tables):
        for table in tables:
            if table is None:
                continue
            table_name = str(getattr(table, "name", "") or "").strip()
            if table_name and table_name in self._table_checked:
                continue
            try:
                table.load()
            except ClientError as exc:
                target_name = table_name or "<unknown>"
                raise ResourceUnavailableError(
                    status_code=503,
                    ui_detail=self._resource_message(
                        resource_kind="DynamoDB table",
                        resource_name=target_name,
                        unavailable=True,
                    ),
                    log_detail=f"{self._resource_message(resource_kind='DynamoDB table', resource_name=target_name, unavailable=False)} precheck failed: {self._format_client_error(exc)}",
                ) from exc
            if table_name:
                self._table_checked.add(table_name)

    @staticmethod
    def _format_client_error(exc: ClientError) -> str:
        error = exc.response.get("Error", {})
        code = str(error.get("Code") or "").strip() or "Unknown"
        message = str(error.get("Message") or "").strip() or str(exc)
        return f"{code}: {message}"

    @staticmethod
    def _resource_message(*, resource_kind: str, resource_name: str, unavailable: bool) -> str:
        if settings.show_resource_name_on_log:
            base = f"{resource_kind} '{resource_name}' associated with this service"
        else:
            article = "an" if resource_kind[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
            base = f"{article} {resource_kind} associated with this service"
        if unavailable:
            return f"{base} is unavailable"
        return base
