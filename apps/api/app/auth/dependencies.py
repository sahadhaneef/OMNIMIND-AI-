import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import digest
from app.core.config import get_settings
from app.core.errors import fail
from app.db.models import (
    AuthSession,
    Membership,
    Organization,
    Role,
    User,
    Workspace,
    WorkspaceMember,
)
from app.db.session import get_db

DB = Annotated[Session, Depends(get_db)]


def current_session(request: Request, db: DB) -> AuthSession:
    token = request.cookies.get(get_settings().cookie_name, "")
    auth = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token)))
    if not auth or auth.revoked_at or auth.expires_at <= datetime.now(UTC):
        raise fail(401, "session_expired")
    user = db.get(User, auth.user_id)
    if not user or user.status != "active":
        raise fail(401, "session_expired")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), auth.csrf_hash):
            raise fail(403, "csrf_invalid")
    return auth


Auth = Annotated[AuthSession, Depends(current_session)]


def org_membership(db: Session, auth: AuthSession, organization_id: uuid.UUID) -> Membership:
    member = db.scalar(
        select(Membership)
        .join(Organization)
        .where(
            Membership.user_id == auth.user_id,
            Membership.organization_id == organization_id,
            Organization.status == "active",
        )
    )
    if not member:
        raise fail(404, "organization_not_found")
    return member


def require_admin(member: Membership) -> None:
    if member.role != Role.ADMIN:
        raise fail(403, "permission_denied")


def authorized_workspace(
    db: Session, auth: AuthSession, organization_id: uuid.UUID, workspace_id: uuid.UUID
) -> tuple[Workspace, Membership]:
    member = org_membership(db, auth, organization_id)
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id, Workspace.organization_id == organization_id
        )
    )
    grant = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.organization_id == organization_id,
            WorkspaceMember.user_id == auth.user_id,
        )
    )
    if not workspace or member.role == Role.OPERATOR or (member.role != Role.ADMIN and not grant):
        raise fail(404, "workspace_not_found")
    return workspace, member
