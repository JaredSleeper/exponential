"""Clerk managed-auth adapter (production).

Flow: the sign-in page loads Clerk.js with CLERK_PUBLISHABLE_KEY; after the
member completes Clerk's email-verified sign-in, the browser POSTs the Clerk
session token to /auth/clerk. We verify it against the instance JWKS and fetch
the verified primary email via the Clerk Backend API (CLERK_SECRET_KEY).

When AUTH_PROVIDER != 'clerk' none of this is used.
"""
import time

import httpx
import jwt
from jwt import PyJWKClient

from .config import get_settings

_jwks: PyJWKClient | None = None
_jwks_at = 0.0


def _jwks_client() -> PyJWKClient:
    global _jwks, _jwks_at
    s = get_settings()
    if _jwks is None or time.time() - _jwks_at > 3600:
        url = s.clerk_jwks_url or _jwks_url_from_key(s.clerk_publishable_key)
        if not url:
            raise RuntimeError("Clerk is not configured (CLERK_JWKS_URL / CLERK_PUBLISHABLE_KEY)")
        _jwks = PyJWKClient(url, cache_keys=True)
        _jwks_at = time.time()
    return _jwks


def _jwks_url_from_key(pk: str) -> str:
    """Derive https://<frontend-api>/.well-known/jwks.json from a publishable key
    (pk_test_/pk_live_ + base64 of the frontend API host)."""
    import base64

    try:
        host = base64.b64decode(pk.split("_", 2)[2] + "==").decode().rstrip("$")
        return f"https://{host}/.well-known/jwks.json"
    except (ValueError, IndexError, UnicodeDecodeError):
        return ""


def verify_session_token(token: str) -> dict:
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(signing_key.key, token, algorithms=["RS256"], options={"verify_aud": False})


def fetch_verified_email(clerk_user_id: str) -> str:
    """Primary email from the Clerk Backend API — must be verified."""
    s = get_settings()
    if not s.clerk_secret_key:
        raise RuntimeError("CLERK_SECRET_KEY is not configured")
    resp = httpx.get(
        f"https://api.clerk.com/v1/users/{clerk_user_id}",
        headers={"Authorization": f"Bearer {s.clerk_secret_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    primary_id = data.get("primary_email_address_id")
    for addr in data.get("email_addresses", []):
        if addr.get("id") == primary_id:
            verified = (addr.get("verification") or {}).get("status") == "verified"
            if not verified:
                raise RuntimeError("Primary email is not verified")
            return addr["email_address"].lower()
    raise RuntimeError("No verified primary email on Clerk user")
