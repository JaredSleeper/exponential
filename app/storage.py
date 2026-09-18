"""Private object storage.

Images are NEVER given public URLs. Both backends are read through the
authenticated /media/<key> route:
- local (dev): files under MEDIA_DIR
- s3: private bucket (works with S3, R2, Tigris, MinIO via S3_ENDPOINT_URL)
"""
import logging
from pathlib import Path

from .config import get_settings

log = logging.getLogger("exponential.storage")

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3

        s = get_settings()
        _s3_client = boto3.client(
            "s3",
            region_name=s.s3_region or None,
            endpoint_url=s.s3_endpoint_url or None,
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
        )
    return _s3_client


def _local_path(key: str) -> Path:
    root = Path(get_settings().media_dir).resolve()
    path = (root / key).resolve()
    if root not in path.parents:  # path traversal guard
        raise ValueError("invalid storage key")
    return path


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    s = get_settings()
    if s.storage_backend == "s3":
        _s3().put_object(Bucket=s.s3_bucket, Key=key, Body=data, ContentType=content_type)
    else:
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def get(key: str) -> tuple[bytes, str]:
    s = get_settings()
    if s.storage_backend == "s3":
        obj = _s3().get_object(Bucket=s.s3_bucket, Key=key)
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")
    path = _local_path(key)
    if not path.exists():
        raise FileNotFoundError(key)
    ctype = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "application/octet-stream"
    return path.read_bytes(), ctype


def delete(key: str) -> None:
    s = get_settings()
    try:
        if s.storage_backend == "s3":
            _s3().delete_object(Bucket=s.s3_bucket, Key=key)
        else:
            _local_path(key).unlink(missing_ok=True)
    except Exception:
        log.exception("storage delete failed key=%s", key)
