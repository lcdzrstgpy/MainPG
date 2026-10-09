from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..basic_settings.service import RuntimeCosConfig


class TemporaryReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class TemporaryReference:
    key: str
    url: str


class TemporaryCosStore:
    """Private COS references for remote image models that cannot consume data URLs."""

    def __init__(self, config: RuntimeCosConfig) -> None:
        self.config = config

    def publish(self, content: bytes, content_type: str) -> TemporaryReference:
        if not self.config.configured:
            raise TemporaryReferenceError("temporary COS reference storage is not configured")
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        except ModuleNotFoundError as exc:
            raise TemporaryReferenceError("COS SDK is not installed") from exc
        key = "/".join((
            "ai-service",
            "transient",
            datetime.now(timezone.utc).strftime("%Y/%m/%d"),
            f"{uuid.uuid4().hex}.image",
        ))
        client = CosS3Client(CosConfig(
            Region=self.config.region,
            SecretId=self.config.secret_id,
            SecretKey=self.config.secret_key,
            Timeout=60,
        ))
        try:
            client.put_object(Bucket=self.config.bucket, Key=key, Body=content, ContentType=content_type, ACL="private")
            url = client.get_presigned_url(Method="GET", Bucket=self.config.bucket, Key=key, Expired=1800)
        except Exception as exc:
            raise TemporaryReferenceError("failed to publish temporary reference image") from exc
        return TemporaryReference(key=key, url=str(url))

    def delete(self, reference: TemporaryReference) -> None:
        if not self.config.configured:
            return
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
            client = CosS3Client(CosConfig(
                Region=self.config.region,
                SecretId=self.config.secret_id,
                SecretKey=self.config.secret_key,
                Timeout=30,
            ))
            client.delete_object(Bucket=self.config.bucket, Key=reference.key)
        except Exception:
            # A periodic cleanup can remove an expired object; generation result must not fail because cleanup did.
            return

    def cleanup_stale(self, older_than_minutes: int = 60) -> int:
        """Best-effort cleanup for objects left by a process interruption.

        Normal requests delete their reference immediately. Startup invokes this
        method so a power loss cannot accumulate customer images in COS.
        """
        if not self.config.configured:
            return 0
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
            client = CosS3Client(CosConfig(
                Region=self.config.region,
                SecretId=self.config.secret_id,
                SecretKey=self.config.secret_key,
                Timeout=30,
            ))
            response = client.list_objects(Bucket=self.config.bucket, Prefix="ai-service/transient/")
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(30, older_than_minutes))
            deleted = 0
            for item in response.get("Contents") or []:
                modified = item.get("LastModified")
                if isinstance(modified, datetime) and modified.astimezone(timezone.utc) < cutoff:
                    client.delete_object(Bucket=self.config.bucket, Key=item["Key"])
                    deleted += 1
            return deleted
        except Exception:
            return 0
