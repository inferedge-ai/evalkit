import re

import httpx

from tests.flows.helpers_01_06 import USERS, api, cli, live, stub_judges_module, token

__all__ = ["cli", "live", "stub_judges_module"]


def test_flow_02_admin_adds_lists_and_archives_users(cli):
    out = cli("users", "add", "dana", input="password\npassword\n")
    assert out.exit_code == 0, out.output
    assert "created dana" in out.output
    USERS["dana"] = "password"
    with api(cli.live, "dana") as dana:
        me = dana.get("/api/auth/me").json()
        assert me["personal_workspace"] == "dana" and me["is_admin"] is False
        ws = dana.get("/api/workspaces/dana").json()
        assert ws["type"] == "personal"
    rows = {line.split()[0]: line for line in cli.ok("users", "list").splitlines() if line.strip()}
    assert {"admin", "alice", "dana"} <= set(rows)
    assert re.search(r"ADMIN\s+created ", rows["admin"])
    assert "created " in rows["dana"] and "ADMIN" not in rows["dana"]

    held = token(cli.live, "dana")
    assert "archived dana" in cli.ok("users", "archive", "dana")
    me = httpx.get(f"{cli.live.url}/api/auth/me", headers={"Authorization": f"Bearer {held}"})
    assert me.status_code == 401
    login = httpx.post(
        f"{cli.live.url}/api/auth/login", json={"username": "dana", "password": "password"}
    )
    assert login.status_code == 401
    assert "ARCHIVED" in next(
        line for line in cli.ok("users", "list").splitlines() if line.startswith("dana")
    )
    with api(cli.live, "admin") as admin:
        assert admin.get("/api/workspaces/dana").json()["archived"] is True
        assert (
            admin.post("/api/users", json={"username": "dana", "password": "password"}).status_code
            == 409
        )

    assert "restored dana" in cli.ok("users", "restore", "dana")
    assert (
        httpx.post(
            f"{cli.live.url}/api/auth/login", json={"username": "dana", "password": "password"}
        ).status_code
        == 200
    )
    me = httpx.get(f"{cli.live.url}/api/auth/me", headers={"Authorization": f"Bearer {held}"})
    assert me.status_code == 401


def test_flow_02_promote_and_demote_toggle_the_admin_flag(cli):
    cli("users", "add", "erin", input="password\npassword\n")
    USERS["erin"] = "password"
    assert "erin is an admin" in cli.ok("users", "promote", "erin")
    with api(cli.live, "erin") as erin:
        assert erin.get("/api/auth/me").json()["is_admin"] is True
        assert erin.get("/api/users").status_code == 200
    assert "erin is no longer an admin" in cli.ok("users", "demote", "erin")
    with api(cli.live, "erin") as erin:
        denied = erin.get("/api/users")
        assert denied.status_code == 403 and denied.json()["detail"] == "admin only"


def test_flow_02_errors(cli):
    with api(cli.live, "admin") as admin:
        duplicate = admin.post("/api/users", json={"username": "alice", "password": "password"})
        assert duplicate.status_code == 409
        clash = admin.post("/api/users", json={"username": "dev", "password": "password"})
        assert clash.status_code == 409
        unknown = admin.post("/api/users/nobody/archive")
        assert unknown.status_code == 404
        last = admin.post("/api/users/admin/demote")
        assert last.status_code == 409 and "last admin" in last.json()["detail"]
    with api(cli.live, "alice") as alice:
        assert (
            alice.post("/api/users", json={"username": "x1", "password": "password"}).status_code
            == 403
        )


def test_flow_02_admins_read_but_cannot_write_personal_workspaces(cli, tmp_path):
    root = cli.suite(
        "mine", {"cases.yaml": "cases:\n  - id: mine-1\n    input: {question: 'q 2024'}\n"}
    )
    cli.as_user("akshat")
    cli.ok("sync", str(root))
    cli.as_user("admin")
    with api(cli.live, "admin") as admin:
        assert admin.get("/api/datasets/mine@akshat/cases").status_code == 200
    out = cli.fails("sync", str(root), "--workspace", "akshat")
    assert "you cannot sync to @akshat" in out
