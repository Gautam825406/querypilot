"""JWT auth (FR-18): pbkdf2 password hashing (stdlib only) + PyJWT tokens.

Roles: admin, analyst, business_user. Workspace scoping is enforced at the
API layer: an authenticated user can only act in their own workspace.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt

from .config import settings

ROLES = ("admin", "analyst", "business_user")
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"pbkdf2_sha256$100000${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, _iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(_iters))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def create_token(user: dict) -> str:
    now = int(time.time())
    payload = {"sub": user["email"], "name": user.get("name", ""),
               "role": user.get("role", "business_user"),
               "workspace": user.get("workspace", "demo"),
               "iat": now, "exp": now + settings.jwt_expiry_hours * 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str):
    """Return the JWT payload, or None when the token is invalid/expired."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except Exception:
        return None
