from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import clerk
from app.config import get_settings


def _make_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, azp):
    return jwt.encode(
        {
            "sub": "user_123",
            "azp": azp,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )


class StubJwksClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.key)


def test_verify_session_token_accepts_valid_token(monkeypatch):
    private_key, public_key = _make_keypair()
    monkeypatch.setattr(clerk, "_jwks_client", lambda: StubJwksClient(public_key))
    azp = get_settings().app_base_url.rstrip("/")
    token = _make_token(private_key, azp)

    claims = clerk.verify_session_token(token)

    assert claims["sub"] == "user_123"
    assert claims["azp"] == azp


def test_verify_session_token_rejects_unexpected_azp(monkeypatch):
    private_key, public_key = _make_keypair()
    monkeypatch.setattr(clerk, "_jwks_client", lambda: StubJwksClient(public_key))
    token = _make_token(private_key, "https://unexpected.example")

    with pytest.raises(jwt.InvalidTokenError, match="unexpected azp"):
        clerk.verify_session_token(token)


def test_verify_session_token_rejects_different_signing_key(monkeypatch):
    private_key, _ = _make_keypair()
    _, public_key = _make_keypair()
    monkeypatch.setattr(clerk, "_jwks_client", lambda: StubJwksClient(public_key))
    token = _make_token(private_key, get_settings().app_base_url.rstrip("/"))

    with pytest.raises(jwt.InvalidTokenError):
        clerk.verify_session_token(token)


def test_verify_session_token_accepts_www_origin(monkeypatch):
    private_key, public_key = _make_keypair()
    monkeypatch.setattr(clerk, "_jwks_client", lambda: StubJwksClient(public_key))
    scheme, _, host = get_settings().app_base_url.rstrip("/").partition("://")
    token = _make_token(private_key, f"{scheme}://www.{host}")

    assert clerk.verify_session_token(token)["sub"] == "user_123"
