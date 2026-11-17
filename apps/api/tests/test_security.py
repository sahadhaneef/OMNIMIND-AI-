from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import digest
from app.db.models import AuthSession, Membership, Role, User, WorkspaceMember
from app.db.session import get_engine
from app.main import app
from tests.conftest import ORIGIN, PASSWORD, Seed, sign_in


def test_authentication_logout_and_replayed_cookie(client: TestClient, seed: Seed) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    for email in ("missing@example.com", "admin@example.com"):
        result = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert result.status_code == 401
        assert result.json()["code"] == "invalid_credentials"
        assert "wrong" not in result.text
    sign_in(client)
    cookie = client.cookies.get("omnimind_session")
    assert cookie and len(cookie) >= 64
    with Session(get_engine()) as db:
        session = db.scalar(select(AuthSession))
        assert session and session.token_hash == digest(cookie)
        assert session.token_hash != cookie
    assert client.get("/api/v1/auth/me").json()["email"] == "admin@example.com"
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set("omnimind_session", cookie)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_expiry_disabled_user_and_rotation(client: TestClient, seed: Seed) -> None:
    sign_in(client)
    old = client.cookies.get("omnimind_session")
    sign_in(client)
    with TestClient(app, headers=ORIGIN) as replay:
        replay.cookies.set("omnimind_session", old)
        assert replay.get("/api/v1/auth/me").status_code == 401
    with Session(get_engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
        assert session
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401
    sign_in(client)
    with Session(get_engine()) as db:
        user = db.get(User, seed.users["admin"].id)
        assert user
        user.status = "disabled"
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_origin_and_cookie_flags(client: TestClient, seed: Seed) -> None:
    result = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    token = result.json()["csrf_token"]
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    client.headers.pop("Origin")
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
        ).status_code
        == 403
    )
    client.headers.update(ORIGIN)
    assert client.get("/api/v1/auth/csrf").json()["csrf_token"] == token
    assert client.get("/api/v1/auth/csrf").json()["csrf_token"] == token
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204


def test_login_limiter_and_validation_redaction(client: TestClient, seed: Seed) -> None:
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": "missing@example.com", "password": "wrong"}
            ).status_code
            == 401
        )
    result = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    assert result.status_code == 429 and result.headers["retry-after"] == "900"
    result = client.post("/api/v1/auth/login", json={"email": "x", "password": "SECRET"})
    assert result.status_code == 422 and "SECRET" not in result.text


@pytest.mark.parametrize(
    "name,create,visible",
    [
        ("admin", 201, 1),
        ("manager", 201, 0),
        ("employee", 403, 1),
        ("operator", 403, 0),
    ],
)
def test_role_matrix(client: TestClient, seed: Seed, name: str, create: int, visible: int) -> None:
    sign_in(client, name)
    result = client.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}")
    assert result.status_code == 200 and len(result.json()["items"]) == visible
    assert (
        client.post(
            "/api/v1/workspaces", json={"organization_id": str(seed.org_a.id), "name": "Created"}
        ).status_code
        == create
    )
    if create == 201:
        assert any(
            w["name"] == "Created"
            for w in client.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}").json()[
                "items"
            ]
        )
    if name != "admin":
        assert client.get(f"/api/v1/organizations/{seed.org_a.id}/members").status_code == 403
        assert (
            client.post(
                f"/api/v1/organizations/{seed.org_a.id}/members",
                json={"user_id": str(seed.users["second"].id), "role": "employee"},
            ).status_code
            == 403
        )


def test_cross_tenant_ids_and_pagination(client: TestClient, seed: Seed) -> None:
    sign_in(client)
    assert [o["id"] for o in client.get("/api/v1/organizations").json()] == [str(seed.org_a.id)]
    assert client.get(f"/api/v1/workspaces?organization_id={seed.org_b.id}").status_code == 404
    assert (
        client.post(
            "/api/v1/workspaces", json={"organization_id": str(seed.org_b.id), "name": "Intrusion"}
        ).status_code
        == 404
    )
    for org in (seed.org_a, seed.org_b):
        path = f"/api/v1/organizations/{org.id}/workspaces/{seed.b.id}"
        assert client.get(path).status_code == 404
        assert client.get(path + "/members").status_code == 404
        assert (
            client.post(
                path + "/members", json={"user_id": str(seed.users["employee"].id)}
            ).status_code
            == 404
        )
        assert client.delete(path + f"/members/{seed.users['employee'].id}").status_code == 404
    assert client.get(f"/api/v1/organizations/{seed.org_b.id}/members").status_code == 404
    assert (
        client.patch(
            f"/api/v1/organizations/{seed.org_b.id}/members/{seed.users['other'].id}",
            json={"role": "employee"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/organizations/{seed.org_b.id}/members/{seed.users['other'].id}"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/workspaces?organization_id={seed.org_a.id}&after={seed.b.id}"
        ).status_code
        == 404
    )
    assert client.get("/api/v1/workspaces?organization_id=invalid").status_code == 422
    client.post("/api/v1/workspaces", json={"organization_id": str(seed.org_a.id), "name": "Next"})
    first = client.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}&limit=1").json()
    second = client.get(
        f"/api/v1/workspaces?organization_id={seed.org_a.id}&after={first['next_cursor']}"
    ).json()
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    sign_in(client, "other")
    assert client.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}").status_code == 404
    assert (
        client.get(f"/api/v1/workspaces?organization_id={seed.org_b.id}").json()["items"][0]["name"]
        == "Beta private"
    )


def test_revocation_duplicates_and_last_admin(client: TestClient, seed: Seed) -> None:
    sign_in(client)
    org_path = f"/api/v1/organizations/{seed.org_a.id}"
    workspace_path = org_path + f"/workspaces/{seed.a.id}"
    assert (
        client.post(
            org_path + "/members",
            json={"user_id": str(seed.users["employee"].id), "role": "employee"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            workspace_path + "/members", json={"user_id": str(seed.users["other"].id)}
        ).status_code
        == 404
    )
    assert (
        client.post(
            workspace_path + "/members", json={"user_id": str(seed.users["employee"].id)}
        ).status_code
        == 409
    )
    with TestClient(app, headers=ORIGIN) as employee:
        sign_in(employee, "employee")
        assert employee.get(workspace_path).status_code == 200
        assert (
            client.delete(workspace_path + f"/members/{seed.users['employee'].id}").status_code
            == 204
        )
        assert employee.get(workspace_path).status_code == 404
        assert (
            employee.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}").json()["items"]
            == []
        )
        assert (
            client.post(
                workspace_path + "/members", json={"user_id": str(seed.users["employee"].id)}
            ).status_code
            == 201
        )
        assert client.delete(org_path + f"/members/{seed.users['employee'].id}").status_code == 204
        assert employee.get(workspace_path).status_code == 404
        assert (
            employee.get(f"/api/v1/workspaces?organization_id={seed.org_a.id}").status_code == 404
        )
        with Session(get_engine()) as db:
            assert (
                db.scalar(
                    select(WorkspaceMember).where(
                        WorkspaceMember.user_id == seed.users["employee"].id
                    )
                )
                is None
            )
    admin_path = org_path + f"/members/{seed.users['admin'].id}"
    assert client.patch(admin_path, json={"role": "employee"}).status_code == 409
    assert client.delete(admin_path).status_code == 409
    assert (
        client.post(
            org_path + "/members",
            json={"user_id": str(seed.users["second"].id), "role": "organization_admin"},
        ).status_code
        == 201
    )
    assert client.patch(admin_path, json={"role": "employee"}).status_code == 200
    assert client.get(org_path + "/members").status_code == 403


def test_database_rejects_invalid_relationships(seed: Seed) -> None:
    with Session(get_engine()) as db:
        for grant in (
            WorkspaceMember(
                organization_id=seed.org_a.id,
                workspace_id=seed.b.id,
                user_id=seed.users["employee"].id,
            ),
            WorkspaceMember(
                organization_id=seed.org_a.id,
                workspace_id=seed.a.id,
                user_id=seed.users["other"].id,
            ),
            WorkspaceMember(
                organization_id=seed.org_a.id,
                workspace_id=seed.a.id,
                user_id=seed.users["employee"].id,
            ),
        ):
            db.add(grant)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        db.add(
            Membership(
                organization_id=seed.org_a.id, user_id=seed.users["admin"].id, role=Role.ADMIN
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_concurrent_admin_demotion(seed: Seed) -> None:
    with Session(get_engine()) as db:
        db.add(
            Membership(
                organization_id=seed.org_a.id, user_id=seed.users["second"].id, role=Role.ADMIN
            )
        )
        db.commit()

    def demote(name: str) -> int:
        with TestClient(app, headers=ORIGIN) as client:
            sign_in(client, name)
            return client.patch(
                f"/api/v1/organizations/{seed.org_a.id}/members/{seed.users[name].id}",
                json={"role": "employee"},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, ["admin", "second"]))
    assert sorted(results) == [200, 409]


def test_health_and_all_protected_routes_require_session(client: TestClient, seed: Seed) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    paths = [
        "/auth/me",
        "/auth/csrf",
        "/organizations",
        f"/organizations/{seed.org_a.id}/members",
        f"/workspaces?organization_id={seed.org_a.id}",
        f"/organizations/{seed.org_a.id}/workspaces/{seed.a.id}",
        f"/organizations/{seed.org_a.id}/workspaces/{seed.a.id}/members",
    ]
    for path in paths:
        assert client.get("/api/v1" + path).status_code == 401
    assert (
        client.post(
            "/api/v1/workspaces", json={"organization_id": str(seed.org_a.id), "name": "Denied"}
        ).status_code
        == 401
    )
