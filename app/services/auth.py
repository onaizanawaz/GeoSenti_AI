"""Authentication and tenancy.

Two rules the rest of the app depends on:

1. `current_user` is the only way to learn who is calling. Routers never read
   a header themselves, so there is one place to get token handling right.
2. A resource belonging to another org is reported as 404, never 403. A 403
   confirms the id exists, which leaks one org's data volume to another.

Passwords use bcrypt directly rather than passlib: one dependency fewer, and
passlib's bcrypt backend has a long history of breaking on bcrypt releases.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

log = logging.getLogger(__name__)

# bcrypt silently truncates at 72 bytes. Silent truncation means "correct
# horse battery staple ..." and a different 100-byte password can share a
# hash, so it is rejected instead.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_CHARS = 8


class AuthError(HTTPException):
    def __init__(self, detail: str = "Not authenticated"):
        super().__init__(status_code=401, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


# ------------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        # A malformed stored hash must read as "wrong password", not crash the
        # login endpoint into a 500 that distinguishes accounts.
        return False


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise HTTPException(
            422, f"Password must be at least {MIN_PASSWORD_CHARS} characters.")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            422, f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
                 f"({len(password.encode())} given). bcrypt ignores anything "
                 f"beyond that, so it is rejected rather than truncated.")


# --------------------------------------------------------------------- tokens

def _secret() -> str:
    s = get_settings()
    if not s.jwt_secret_key:
        raise HTTPException(
            500,
            "JWT_SECRET_KEY is not set. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
            "and put it in .env.")
    return s.jwt_secret_key


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        # org travels in the token, but is never trusted from it -- see
        # current_user, which re-reads the user row every request.
        "org": str(user.org_id),
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_expire_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(token, _secret(), algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise AuthError("Token has expired.")
    except jwt.InvalidTokenError:
        raise AuthError("Token is invalid.")


# ---------------------------------------------------------------- dependencies

def current_user(authorization: str | None = Header(default=None),
                 db: Session = Depends(get_db)) -> User:
    """Resolve the bearer token to a live user row.

    The row is re-read every request rather than trusting the token's claims,
    so deactivating a user or moving them between orgs takes effect at once
    instead of whenever their token happens to expire.
    """
    if not authorization:
        raise AuthError("Missing Authorization header.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization header must be 'Bearer <token>'.")

    sub = decode_token(token).get("sub")
    user = db.query(User).filter_by(id=sub).first() if sub else None

    if user is None:
        raise AuthError("Token subject no longer exists.")
    if not user.is_active:
        raise AuthError("This account is disabled.")
    return user


def require_owner(user: User = Depends(current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(403, "This action requires the org owner role.")
    return user