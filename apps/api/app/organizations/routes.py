import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth.dependencies import DB, Auth, org_membership, require_admin
from app.core.errors import fail
from app.db.models import Membership, Organization, Role, User

router = APIRouter(prefix="/organizations", tags=["organizations"])


class MemberInput(BaseModel):
    user_id: uuid.UUID
    role: Literal["organization_admin", "knowledge_manager", "employee", "platform_operator"]


class RoleInput(BaseModel):
    role: Literal["organization_admin", "knowledge_manager", "employee", "platform_operator"]


@router.get("")
def organizations(auth: Auth, db: DB) -> list[dict[str, str]]:
    rows = db.execute(
        select(Organization, Membership)
        .join(Membership)
        .where(Membership.user_id == auth.user_id, Organization.status == "active")
        .order_by(Organization.name, Organization.id)
    )
    return [{"id": str(org.id), "name": org.name, "role": member.role} for org, member in rows]


def lock_admin(db: DB, auth: Auth, organization_id: uuid.UUID) -> None:
    # Serialize all membership changes, including concurrent last-admin demotions.
    db.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    require_admin(org_membership(db, auth, organization_id))


@router.get("/{organization_id}/members")
def members(organization_id: uuid.UUID, auth: Auth, db: DB) -> list[dict[str, str]]:
    require_admin(org_membership(db, auth, organization_id))
    rows = db.execute(
        select(Membership, User)
        .join(User)
        .where(Membership.organization_id == organization_id)
        .order_by(User.email)
    )
    return [{"user_id": str(u.id), "email": u.email, "role": m.role} for m, u in rows]


@router.post("/{organization_id}/members", status_code=201)
def add_member(organization_id: uuid.UUID, body: MemberInput, auth: Auth, db: DB) -> dict[str, str]:
    lock_admin(db, auth, organization_id)
    user = db.get(User, body.user_id)
    if not user or user.status != "active":
        raise fail(404, "user_not_found")
    db.add(Membership(organization_id=organization_id, user_id=body.user_id, role=body.role))
    db.commit()
    return {"user_id": str(body.user_id), "role": body.role}


def target_member(db: DB, organization_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    target = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == user_id
        )
    )
    if not target:
        raise fail(404, "membership_not_found")
    return target


def protect_last_admin(db: DB, target: Membership) -> None:
    user = db.get(User, target.user_id)
    if target.role == Role.ADMIN and user and user.status == "active":
        count = db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User)
            .where(
                Membership.organization_id == target.organization_id,
                Membership.role == Role.ADMIN,
                User.status == "active",
            )
        )
        if count == 1:
            raise fail(409, "last_active_administrator")


@router.patch("/{organization_id}/members/{user_id}")
def update_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, body: RoleInput, auth: Auth, db: DB
) -> dict[str, str]:
    lock_admin(db, auth, organization_id)
    target = target_member(db, organization_id, user_id)
    if body.role != Role.ADMIN.value:
        protect_last_admin(db, target)
    target.role = body.role
    db.commit()
    return {"user_id": str(user_id), "role": body.role}


@router.delete("/{organization_id}/members/{user_id}", status_code=204)
def remove_member(organization_id: uuid.UUID, user_id: uuid.UUID, auth: Auth, db: DB) -> None:
    lock_admin(db, auth, organization_id)
    target = target_member(db, organization_id, user_id)
    protect_last_admin(db, target)
    db.delete(target)
    db.commit()
