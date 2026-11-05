from datetime import UTC, datetime, timedelta

import httpx

from evalkit import version
from evalkit.client import connection
from tests.flows.helpers_01_06 import api, cli, live, stub_judges_module

__all__ = ["cli", "live", "stub_judges_module"]


def test_flow_01_cli_info_is_public_and_responses_carry_the_version(live):
    info = httpx.get(f"{live.url}/api/cli")
    assert info.status_code == 200
    assert info.json() == {"version": version.current(), "package": "evalkit-py"}
    assert info.headers["X-Evalkit-Version"] == version.current()
    assert httpx.get(f"{live.url}/api/nope").headers["X-Evalkit-Version"] == version.current()


def test_flow_01_login_saves_one_server_and_a_new_login_replaces_it(cli, monkeypatch):
    monkeypatch.delenv("EVALKIT_SERVER_URL")
    monkeypatch.delenv("EVALKIT_TOKEN")
    url = cli.live.url
    out = cli.ok("login", "--url", url, "--username", "admin", "--password", "hunter2-pw")
    assert f"logged in as admin on {url}; saved to" in out
    assert f"admin on {url}" in cli.ok("whoami")
    cli.ok("login", "--url", url, "--username", "alice", "--password", "password")
    assert f"alice on {url}" in cli.ok("whoami")
    stored = connection.load()
    assert stored is not None and (stored.url, stored.username) == (url, "alice")

    assert cli("--profile", "dev", "whoami").exit_code == 2
    assert cli("profiles", "list").exit_code == 2


def test_flow_01_old_profiles_file_asks_for_a_fresh_login(cli, monkeypatch):
    monkeypatch.delenv("EVALKIT_SERVER_URL")
    monkeypatch.delenv("EVALKIT_TOKEN")
    connection.CONFIG_PATH.write_text(
        f'default = "dev"\n\n[profiles.dev]\nurl = "{cli.live.url}"\ntoken = "t"\n'
    )
    out = cli.fails("whoami")
    assert "holds server profiles, which evalkit no longer supports" in out
    assert "run `evalkit login --url <url>`" in out


def test_flow_01_no_login_and_env_override(cli, monkeypatch):
    monkeypatch.delenv("EVALKIT_SERVER_URL")
    monkeypatch.delenv("EVALKIT_TOKEN")
    out = cli.fails("whoami")
    assert "no server configured" in out and "EVALKIT_SERVER_URL" in out
    cli.as_user("ravi")
    out = cli.ok("whoami")
    assert f"ravi on {cli.live.url} (from EVALKIT_SERVER_URL)" in out


def test_flow_01_whoami_shows_expiry_after_the_configured_ttl(cli):
    out = cli.ok("whoami")
    expiry = datetime.strptime(
        out.split("token expires ")[1].strip()[:16], "%Y-%m-%d %H:%M"
    ).replace(tzinfo=UTC)
    assert abs(expiry - (datetime.now(UTC) + timedelta(hours=720))) < timedelta(minutes=5)


def test_flow_01_expiry_warning_within_a_day(cli, monkeypatch):
    real = connection.token_expiry_warning
    monkeypatch.setattr(
        connection,
        "token_expiry_warning",
        lambda p, at, now: real(p, at, now + timedelta(days=29, hours=12)),
    )
    out = cli.ok("whoami")
    assert f"the token for {cli.live.url} expires in" in out and "before a long run" in out


def test_flow_01_version_mismatch_warns_or_stops(cli, monkeypatch):
    server = version.parse(version.current())
    patch = f"{server.major}.{server.minor}.{server.patch + 1}"
    monkeypatch.setattr(version, "current", lambda: patch)
    out = cli.ok("whoami")
    assert "differs from server" in out and "run `evalkit update --server http" in out
    for other in (f"{server.major}.{server.minor + 1}.0", f"{server.major + 1}.0.0"):
        monkeypatch.setattr(version, "current", lambda other=other: other)
        out = cli.fails("whoami")
        assert "cannot talk to server" in out


def test_flow_01_auth_errors_and_admin_flag(live):
    wrong = httpx.post(f"{live.url}/api/auth/login", json={"username": "alice", "password": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "invalid username or password"
    invalid = httpx.get(f"{live.url}/api/auth/me", headers={"Authorization": "Bearer junk"})
    assert invalid.status_code == 401 and invalid.json()["detail"] == "invalid token"
    with api(live, "admin") as admin, api(live, "ravi") as ravi, api(live, "alice") as alice:
        assert admin.get("/api/auth/me").json()["is_admin"] is True
        me = ravi.get("/api/auth/me").json()
        assert me["is_admin"] is False
        assert me["roles"] == {"dev": ["reviewer"]}
        assert me["personal_workspace"] == "ravi"
        assert alice.get("/api/auth/me").json()["roles"] == {"dev": ["writer"]}
