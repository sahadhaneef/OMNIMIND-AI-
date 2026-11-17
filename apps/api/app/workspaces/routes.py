import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.dependencies import (
    DB,
    Auth,
    authorized_workspace,
    org_membership,
    require_admin,
)
from app.core.errors import fail
from app.db.models import Membership, Organization, Role, Workspace, WorkspaceMember
from app.organizations.routes import lock_admin

router = APIRouter(tags=["workspaces"])


class WorkspaceInput(BaseModel):
    organization_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(str_strip_whitespace=True)


class GrantInput(BaseModel):
    user_id: uuid.UUID


def serialize(workspace: Workspace) -> dict[str, str]:
    return {
        "id": str(workspace.id),
        "organization_id": str(workspace.organization_id),
        "name": workspace.name,
    }


@router.get("/workspaces")
def list_workspaces(
    organization_id: uuid.UUID,
    auth: Auth,
    db: DB,
    after: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    member = org_membership(db, auth, organization_id)
    if after:
        # Validate cursor ownership even for a role that cannot list workspaces.
        authorized_workspace(db, auth, organization_id, after)
    query = select(Workspace).where(Workspace.organization_id == organization_id)
    if member.role == Role.OPERATOR:
        return {"items": [], "next_cursor": None}
    if member.role != Role.ADMIN:
        query = query.join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id).where(
            WorkspaceMember.user_id == auth.user_id,
            WorkspaceMember.organization_id == organization_id,
        )
    if after:
        query = query.where(Workspace.id > after)
    rows = list(db.scalars(query.order_by(Workspace.id).limit(limit + 1)))
    return {
        "items": [serialize(w) for w in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceInput, auth: Auth, db: DB) -> dict[str, str]:
    # Same org lock as membership revocation: role cannot change mid-creation.
    db.scalar(select(Organization).where(Organization.id == body.organization_id).with_for_update())
    member = org_membership(db, auth, body.organization_id)
    if member.role not in {Role.ADMIN, Role.MANAGER}:
        raise fail(403, "permission_denied")
    workspace = Workspace(organization_id=body.organization_id, name=body.name)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            organization_id=body.organization_id, workspace_id=workspace.id, user_id=auth.user_id
        )
    )
    db.commit()
    return serialize(workspace)


@router.get("/organizations/{organization_id}/workspaces/{workspace_id}")
def get_workspace(
    organization_id: uuid.UUID, workspace_id: uuid.UUID, auth: Auth, db: DB
) -> dict[str, str]:
    workspace, _ = authorized_workspace(db, auth, organization_id, workspace_id)
    return serialize(workspace)


@router.get("/organizations/{organization_id}/workspaces/{workspace_id}/members")
def members(
    organization_id: uuid.UUID, workspace_id: uuid.UUID, auth: Auth, db: DB
) -> list[dict[str, str]]:
    _, member = authorized_workspace(db, auth, organization_id, workspace_id)
    require_admin(member)
    rows = db.scalars(
        select(WorkspaceMember).where(
            WorkspaceMember.organization_id == organization_id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    )
    return [{"user_id": str(row.user_id), "role": row.role} for row in rows]


@router.post("/organizations/{organization_id}/workspaces/{workspace_id}/members", status_code=201)
def grant(
    organization_id: uuid.UUID, workspace_id: uuid.UUID, body: GrantInput, auth: Auth, db: DB
) -> dict[str, str]:
    lock_admin(db, auth, organization_id)
    authorized_workspace(db, auth, organization_id, workspace_id)
    target = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == body.user_id
        )
    )
    if not target or target.role == Role.OPERATOR:
        raise fail(404, "membership_not_found")
    db.add(
        WorkspaceMember(
            organization_id=organization_id, workspace_id=workspace_id, user_id=body.user_id
        )
    )
    db.commit()
    return {"user_id": str(body.user_id), "role": "member"}


@router.delete(
    "/organizations/{organization_id}/workspaces/{workspace_id}/members/{user_id}", status_code=204
)
def revoke(
    organization_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID, auth: Auth, db: DB
) -> None:
    lock_admin(db, auth, organization_id)
    authorized_workspace(db, auth, organization_id, workspace_id)
    grant = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.organization_id == organization_id,
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not grant:
        raise fail(404, "workspace_membership_not_found")
    db.delete(grant)
    db.commit()
