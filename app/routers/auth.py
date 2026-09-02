"""Registration, login and identity.

Registration creates an org and its owner in one transaction: a user without an
org cannot be scoped, so the two never exist apart. Everyone after the first
joins through /auth/invite, which takes the org from the caller's token.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Org, User
from app.schemas import (InviteRequest, LoginRequest, RegisterRequest, TokenOut,
                         UserOut)
from app.services.auth import (create_access_token, current_user, hash_password,
                               require_owner, validate_password, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])

ROLES = ("owner", "member")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while db.query(Org).filter_by(slug=slug).first() is not None:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _user_out(user: User, org: Org) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, role=user.role,
                   org_id=str(org.id), org_name=org.name)


def _token_out(user: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(user),
                    expires_in=get_settings().access_token_expire_minutes * 60)


@router.post("/register", response_model=TokenOut)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create an org and its first owner."""
    validate_password(payload.password)

    if db.query(User).filter_by(email=payload.email.lower()).first():
        # The email is already visible to whoever submitted it, so saying so
        # is not a disclosure -- and a vague error here just produces support
        # tickets. Login stays deliberately vague; registration need not.
        raise HTTPException(409, "An account with this email already exists.")

    org = Org(name=payload.org_name, slug=_unique_slug(db, payload.org_name))
    db.add(org)
    db.flush()          # need org.id before the user, same transaction

    user = User(org_id=org.id, email=payload.email.lower(),
                password_hash=hash_password(payload.password), role="owner")
    db.add(user)
    db.commit()
    db.refresh(user)
    return _token_out(user)


@router.post("/invite", response_model=UserOut)
def invite(payload: InviteRequest, caller: User = Depends(require_owner),
           db: Session = Depends(get_db)):
    """Add a user to the caller's own org. Owner only."""
    if payload.role not in ROLES:
        raise HTTPException(422, f"role must be one of {list(ROLES)}.")
    validate_password(payload.password)

    if db.query(User).filter_by(email=payload.email.lower()).first():
        raise HTTPException(409, "An account with this email already exists.")

    # org_id comes from the caller's token, never from the request body.
    user = User(org_id=caller.org_id, email=payload.email.lower(),
                password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user, db.query(Org).filter_by(id=caller.org_id).first())


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=payload.email.lower()).first()

    # One message and one code for every failure mode. A distinct "no such
    # user" reply turns this endpoint into an account enumerator.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    if not user.is_active:
        raise HTTPException(401, "Incorrect email or password.")

    return _token_out(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _user_out(user, db.query(Org).filter_by(id=user.org_id).first())