class ResourceGuard:
    def __init__(self, *, s3_client):
        self.s3 = s3_client
        self._bucket_checked = set()
        self._table_checked = set()

    def s3_bucket(self, bucket_name: str):
        normalized_bucket = str(bucket_name or "").strip()
        if not normalized_bucket or normalized_bucket in self._bucket_checked:
            return
        self.s3.head_bucket(Bucket=normalized_bucket)
        self._bucket_checked.add(normalized_bucket)

    def tables(self, *tables):
        for table in tables:
            if table is None:
                continue
            table_name = str(getattr(table, "name", "") or "").strip()
            if table_name and table_name in self._table_checked:
                continue
            table.load()
            if table_name:
                self._table_checked.add(table_name)
