import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import provision
from app.db.models import Membership, Organization, User
from app.db.session import get_engine
from tests.conftest import PASSWORD


def test_provision_is_repeatable_and_can_login(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "--organization",
            "Provisioned",
            "--slug",
            "provisioned",
            "--email",
            "New@Example.com",
        ],
    )
    prompts: Iterator[str] = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(provision.getpass, "getpass", lambda _: next(prompts))
    provision.main()
    provision.main()  # Would fail if credentials were requested again.
    with Session(get_engine()) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert db.scalar(select(func.count()).select_from(Membership)) == 1
    result = client.post(
        "/api/v1/auth/login", json={"email": "new@example.com", "password": PASSWORD}
    )
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    org = client.get("/api/v1/organizations").json()[0]
    assert (
        client.post(
            "/api/v1/workspaces",
            json={"organization_id": org["id"], "name": "Provisioned workspace"},
        ).status_code
        == 201
    )
    assert client.post("/api/v1/auth/logout").status_code == 204
