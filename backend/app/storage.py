"""Cloudflare R2 (S3-compatible) helper for serving downloadable files.

A delivery rule's `download_url` can be either:
  - a full URL (http…)      → used as-is (any public host)
  - an R2 object key        → turned into a short-lived signed URL here

Signed URLs expire quickly (download_presign_ttl_seconds), so even if a buyer
grabs the final link it stops working — they can't reshare the file.
"""
from functools import lru_cache

from .config import get_settings

settings = get_settings()


@lru_cache
def _client():
    """Lazily build the S3 client (R2 / Supabase / any S3-compatible host)."""
    if not (settings.r2_access_key_id and settings.r2_secret_access_key and settings.r2_bucket):
        return None
    endpoint = settings.r2_endpoint or (
        f"https://{settings.r2_account_id}.r2.cloudflarestorage.com" if settings.r2_account_id else ""
    )
    if not endpoint:
        return None
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name=settings.r2_region or "auto",
        config=Config(signature_version="s3v4"),
    )


def is_configured() -> bool:
    return _client() is not None


def is_object_key(download_url: str) -> bool:
    """True when the value is an R2 object key rather than a full URL."""
    return bool(download_url) and not download_url.lower().startswith(("http://", "https://"))


def presigned_url(key: str) -> str:
    """A short-lived signed GET URL for an R2 object key."""
    client = _client()
    if client is None:
        raise RuntimeError("R2 is not configured")
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket, "Key": key.lstrip("/")},
        ExpiresIn=settings.download_presign_ttl_seconds,
    )
