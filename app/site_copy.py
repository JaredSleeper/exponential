"""Editable public-page copy storage and cache."""
import re
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SiteCopy, utcnow

KEY_RE = re.compile(r"^[a-z0-9_.-]{1,80}$")
MAX_LEN = 2000
CACHE_TTL = 30.0

_cache: dict[str, str] | None = None
_cache_expires_at = 0.0
_cache_lock = threading.Lock()


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise ValueError("Invalid copy key")
    return key


def load_overrides(db: Session) -> dict[str, str]:
    global _cache, _cache_expires_at
    with _cache_lock:
        now = time.monotonic()
        if _cache is None or now >= _cache_expires_at:
            _cache = {
                row.key: row.value
                for row in db.scalars(select(SiteCopy)).all()
            }
            _cache_expires_at = now + CACHE_TTL
        return dict(_cache)


def invalidate() -> None:
    global _cache, _cache_expires_at
    with _cache_lock:
        _cache = None
        _cache_expires_at = 0.0


def set_copy(db: Session, key: str, value: str, actor_email: str) -> None:
    key = validate_key(key)
    row = db.get(SiteCopy, key)
    if row is None:
        db.add(SiteCopy(key=key, value=value, updated_by=actor_email))
    else:
        row.value = value
        row.updated_by = actor_email
        row.updated_at = utcnow()


def delete_copy(db: Session, key: str) -> None:
    row = db.get(SiteCopy, validate_key(key))
    if row is not None:
        db.delete(row)
