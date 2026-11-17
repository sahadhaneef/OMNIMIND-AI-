import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.auth.dependencies import DB, Auth
from app.auth.security import DUMMY_HASH, digest, hasher, verify
from app.core.config import get_settings
from app.core.errors import fail
from app.db.models import AuthSession, LoginBucket, User

router = APIRouter(prefix="/auth", tags=["authentication"])


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: DB) -> dict[str, str]:
    settings = get_settings()
    now = datetime.now(UTC)
    # Socket peer only: never trust caller-supplied X-Forwarded-For.
    peer = request.client.host if request.client else "unknown"
    start = datetime.fromtimestamp(
        int(now.timestamp()) // settings.login_window_seconds * settings.login_window_seconds, UTC
    )
    limited = False
    for identity in (f"ip:{peer}", f"email:{body.email}"):
        key = digest(f"{identity}:{start.isoformat()}")
        attempts = db.scalar(
            insert(LoginBucket)
            .values(key=key, window_start=start, attempts=1)
            .on_conflict_do_update(
                index_elements=[LoginBucket.key], set_={"attempts": LoginBucket.attempts + 1}
            )
            .returning(LoginBucket.attempts)
        )
        limited = limited or (attempts is not None and attempts > settings.login_limit)
    db.execute(
        delete(LoginBucket).where(
            LoginBucket.window_start < now - timedelta(seconds=settings.login_window_seconds * 2)
        )
    )
    db.commit()  # Persist unsuccessful attempts, too.
    if limited:
        from fastapi import HTTPException

        raise HTTPException(
            429, "login_rate_limited", headers={"Retry-After": str(settings.login_window_seconds)}
        )
    user = db.scalar(select(User).where(User.email == body.email))
    valid = verify(user.password_hash if user else DUMMY_HASH, body.password)
    if not user or not valid or user.status != "active":
        raise fail(401, "invalid_credentials")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(body.password)
    old_token = request.cookies.get(settings.cookie_name)
    if old_token:
        old = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(old_token)))
        if old:
            old.revoked_at = now
    token = secrets.token_urlsafe(48)
    csrf = digest("csrf:" + token)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    db.commit()
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"id": str(user.id), "email": user.email, "csrf_token": csrf}


@router.get("/me")
def me(auth: Auth, db: DB) -> dict[str, str]:
    user = db.get(User, auth.user_id)
    assert user is not None
    return {"id": str(user.id), "email": user.email}


@router.get("/csrf")
def csrf(request: Request, auth: Auth) -> dict[str, str]:
    # Stable across tabs; only same-origin JavaScript can read this response.
    token = request.cookies[get_settings().cookie_name]
    return {"csrf_token": digest("csrf:" + token)}


@router.post("/logout", status_code=204)
def logout(auth: Auth, db: DB, response: Response) -> None:
    auth.revoked_at = datetime.now(UTC)
    db.commit()
    response.delete_cookie(
        get_settings().cookie_name,
        path="/",
        secure=get_settings().cookie_secure,
        httponly=True,
        samesite="lax",
    )
