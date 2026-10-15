"""S3 호환 객체 저장소에 데이터셋의 불변 파일을 보관한다."""

import hashlib
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import Settings


class ObjectStore:
    """데이터 위치가 실행 머신의 로컬 경로에 종속되지 않도록 한다."""

    def __init__(self, settings: Settings | None = None):
        self.config = settings or Settings.from_env()
        self.client = boto3.client(
            "s3",
            endpoint_url=self.config.s3_endpoint,
            aws_access_key_id=self.config.s3_access_key,
            aws_secret_access_key=self.config.s3_secret_key,
            region_name=self.config.s3_region,
            config=BotoConfig(
                s3={"addressing_style": "path"},
                connect_timeout=2,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        )

    def ready(self) -> bool:
        self.client.head_bucket(Bucket=self.config.s3_bucket)
        return True

    def ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.config.s3_bucket)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") not in (403, 404):
                raise
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 403:
                raise
            self.client.create_bucket(Bucket=self.config.s3_bucket)

    def put(self, key: str, source: Path) -> str:
        digest = hashlib.sha256()
        with source.open("rb") as data:
            for chunk in iter(lambda: data.read(1024 * 1024), b""):
                digest.update(chunk)
        self.client.upload_file(str(source), self.config.s3_bucket, key)
        return digest.hexdigest()

    def get(self, key: str, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Preview·Profile이 동시에 같은 스냅샷을 읽어도 임시 파일이 충돌하지 않는다.
        pending = destination.with_name(destination.name + f".pending-{uuid4().hex}")
        try:
            self.client.download_file(self.config.s3_bucket, key, str(pending))
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)

    def list_page(
        self, prefix: str, *, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Iceberg를 제외한 소유 접두사만 페이지 단위로 조회한다."""
        if prefix not in ("datasets/", "results/") or not 1 <= limit <= 1000:
            raise ValueError("Invalid object inventory scope")
        options = {"Bucket": self.config.s3_bucket, "Prefix": prefix, "MaxKeys": limit}
        if cursor:
            options["ContinuationToken"] = cursor
        response = self.client.list_objects_v2(**options)
        objects = [
            {
                "location": self.location(item["Key"]),
                "size_bytes": item["Size"],
                "modified_at": item["LastModified"].isoformat(),
                # ETag 단독으로는 생성 세대를 확정할 수 없어 시각·크기를 함께 사용한다.
                "fingerprint": (
                    hashlib.sha256(
                        f"{item['ETag']}|{item['Size']}|{item['LastModified'].isoformat()}".encode()
                    ).hexdigest()
                    if item.get("ETag")
                    else None
                ),
            }
            for item in response.get("Contents", [])
            if item["Key"].startswith(prefix)
        ]
        return objects, response.get("NextContinuationToken")

    def location(self, key: str) -> str:
        return f"s3://{self.config.s3_bucket}/{key}"

    def key(self, location: str) -> str:
        prefix = f"s3://{self.config.s3_bucket}/"
        if not location.startswith(prefix):
            raise ValueError("Dataset is outside the configured object store")
        key = location[len(prefix) :]
        if not key or ".." in key.split("/"):
            raise ValueError("Invalid object key")
        return key
