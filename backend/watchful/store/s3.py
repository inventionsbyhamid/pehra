import time

import boto3

from watchful.registry import register


@register("blobs", "s3")
class S3BlobStore:
    """Clips, keyframes and uploads in S3; the bucket's lifecycle rule also expires them."""

    def __init__(self, settings=None, bucket: str | None = None, region: str | None = None, name: str = "s3",
                 url_ttl_s: int = 3600, **_):
        self.bucket = bucket or settings.s3_bucket
        self.s3 = boto3.client("s3", region_name=region or settings.aws_region)
        self.url_ttl_s = url_ttl_s

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey as e:
            raise KeyError(key) from e

    def url(self, key: str) -> str:
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=self.url_ttl_s)

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        """Every object under `prefix` (a society's `{workspace_id}/`, when the demo reset deletes a sandbox)."""
        if not prefix.endswith("/"):
            raise ValueError("a prefix ends with /")
        removed = 0
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            for i in range(0, len(keys), 1000):
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys[i:i + 1000]})
                removed += len(keys[i:i + 1000])
        return removed

    def delete_older_than(self, days: int, keep: tuple[str, ...] = ("people/",)) -> int:
        cutoff, removed = time.time() - days * 86400, 0
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            old = [{"Key": o["Key"]} for o in page.get("Contents", [])
                   if o["LastModified"].timestamp() < cutoff and not o["Key"].startswith(keep)]
            for i in range(0, len(old), 1000):
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": old[i:i + 1000]})
                removed += len(old[i:i + 1000])
        return removed
