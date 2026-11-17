import os
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

os.environ["ENVIRONMENT"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ALLOWED_ORIGINS"] = '["http://localhost:5173"]'
os.environ["ALLOWED_HOSTS"] = '["testserver","localhost"]'
if "TEST_DATABASE_URL" not in os.environ:
    raise RuntimeError("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from app.auth.security import hasher  # noqa: E402
from app.db.models import (  # noqa: E402
    Membership,
    Organization,
    Role,
    User,
    Workspace,
    WorkspaceMember,
)
from app.db.session import get_engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "correct-horse-battery-2026"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture(scope="session", autouse=True)
def migrate() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean(migrate: None) -> Generator[None]:
    with get_engine().begin() as db:
        db.execute(
            text(
                "TRUNCATE workspace_members, workspaces, memberships, organizations, "
                "auth_sessions, users, login_buckets CASCADE"
            )
        )
    yield


@dataclass
class Seed:
    org_a: Organization
    org_b: Organization
    users: dict[str, User]
    a: Workspace
    b: Workspace


@pytest.fixture
def seed() -> Seed:
    with Session(get_engine(), expire_on_commit=False) as db:
        org_a = Organization(name="Alpha", slug="alpha")
        org_b = Organization(name="Beta", slug="beta")
        users = {
            name: User(email=f"{name}@example.com", password_hash=hasher.hash(PASSWORD))
            for name in ("admin", "manager", "employee", "operator", "other", "second")
        }
        db.add_all([org_a, org_b, *users.values()])
        db.flush()
        for name, role in (
            ("admin", Role.ADMIN),
            ("manager", Role.MANAGER),
            ("employee", Role.EMPLOYEE),
            ("operator", Role.OPERATOR),
        ):
            db.add(Membership(organization_id=org_a.id, user_id=users[name].id, role=role))
        db.add(Membership(organization_id=org_b.id, user_id=users["other"].id, role=Role.ADMIN))
        db.flush()
        a = Workspace(organization_id=org_a.id, name="Alpha private")
        b = Workspace(organization_id=org_b.id, name="Beta private")
        db.add_all([a, b])
        db.flush()
        db.add(
            WorkspaceMember(
                organization_id=org_a.id, workspace_id=a.id, user_id=users["employee"].id
            )
        )
        db.commit()
        return Seed(org_a, org_b, users, a, b)


@pytest.fixture
def client() -> Generator[TestClient]:
    with TestClient(app) as client:
        client.headers.update(ORIGIN)
        yield client


def sign_in(client: TestClient, name: str = "admin") -> dict[str, Any]:
    result = client.post(
        "/api/v1/auth/login", json={"email": f"{name}@example.com", "password": PASSWORD}
    )
    assert result.status_code == 200, result.text
    body: dict[str, Any] = result.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body
