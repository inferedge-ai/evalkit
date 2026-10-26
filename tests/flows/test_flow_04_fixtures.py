import hashlib

import httpx

from evalkit.core.blob import fixture_blob_key
from tests.flows.helpers_01_06 import RUBRICS, api, cli, live, stub_judges_module, sync_counts

__all__ = ["cli", "live", "stub_judges_module"]


def fixture_case(case_id: str, key: str) -> str:
    return (
        f"cases:\n  - id: {case_id}\n    fixtures: [{key}]\n"
        f"    input: {{question: 'Net debt as of 2024-01-01 ({case_id})?'}}\n{RUBRICS}"
    )


def registry(key: str, digest: str, context: str = "Balance sheet.") -> str:
    return f"fixtures:\n- key: {key}\n  sha256: '{digest}'\n  context: {context}\n"


def test_flow_04_sync_uploads_missing_bytes_once_and_ci_reuses_them(cli):
    data = b"line,value\nnet_debt,42\n"
    digest = hashlib.sha256(data).hexdigest()
    (cli.tmp_path / "fixtures" / "bs4.csv").write_bytes(data)
    cli.as_user("akshat")
    with api(cli.live, "akshat") as akshat:
        assert akshat.head(f"/api/fixtures/sha256/{digest}").status_code == 404
        cli.suite("fx4", {"a.yaml": fixture_case("fx4-1", "bs4.csv")}, registry("bs4.csv", digest))
        cli.ok("sync", "fx4")
        assert akshat.head(f"/api/fixtures/sha256/{digest}").status_code == 200
        assert cli.live.blob.objects[fixture_blob_key(digest)] == data
        [case] = akshat.get("/api/datasets/fx4@akshat/cases").json()
        assert case["files"] == [{"key": "bs4.csv", "sha256": digest, "context": "Balance sheet."}]
        assert "workspace" not in case["files"][0]

    (cli.tmp_path / "fixtures" / "bs4.csv").unlink()
    cli.as_user("alice")
    out = cli.ok("sync", "fx4", "--workspace", "dev")
    assert sync_counts(out)["created"] == 1 and "promoted" not in out
    with api(cli.live, "alice") as alice:
        [case] = alice.get("/api/datasets/fx4@dev/cases").json()
        assert case["files"][0]["sha256"] == digest
        fixtures = alice.get("/api/fixtures", params={"workspace": "dev"}).json()
        assert [(f["key"], f["sha256"]) for f in fixtures["items"]] == [("bs4.csv", digest)]
        assert fixtures["items"][0]["datasets"] == ["fx4"]


def test_flow_04_registry_mismatch_and_missing_bytes_fail_before_the_sync(cli):
    cli.as_user("akshat")
    (cli.tmp_path / "fixtures" / "bad4.csv").write_bytes(b"real bytes")
    cli.suite("mm4", {"a.yaml": fixture_case("mm4-1", "bad4.csv")}, registry("bad4.csv", "0" * 64))
    out = cli.fails("sync", "mm4")
    assert "fixture 'bad4.csv' on disk has sha256" in out and "update the registry entry" in out

    cli.suite(
        "gone4", {"a.yaml": fixture_case("gone4-1", "gone4.csv")}, registry("gone4.csv", "1" * 64)
    )
    out = cli.fails("sync", "gone4")
    assert "fixture 'gone4.csv' is registered but" in out and "the server has no bytes" in out
    with api(cli.live, "akshat") as akshat:
        assert akshat.get("/api/datasets/mm4@akshat").status_code == 404
        assert akshat.get("/api/datasets/gone4@akshat").status_code == 404


def test_flow_04_any_user_uploads_and_downloads_by_sha256(live):
    data = b"any user may upload"
    digest = hashlib.sha256(data).hexdigest()
    with api(live, "akshat") as akshat:
        bad = akshat.put(f"/api/fixtures/sha256/{'2' * 64}", content=data)
        assert bad.status_code == 400
        assert akshat.put(f"/api/fixtures/sha256/{digest}", content=data).status_code == 200
        got = akshat.get(f"/api/fixtures/sha256/{digest}", params={"name": "u.csv"})
        assert got.status_code == 200 and got.content == data
        assert "u.csv" in got.headers["content-disposition"]
        listed = akshat.get("/api/fixtures", params={"workspace": "akshat"}).json()["items"]
        assert digest not in {f["sha256"] for f in listed}

        ticket = akshat.post("/api/auth/download-ticket").json()["ticket"]
    url = f"{live.url}/api/fixtures/sha256/{digest}"
    first = httpx.get(url, params={"ticket": ticket})
    assert first.status_code == 200 and first.content == data
    assert httpx.get(url, params={"ticket": ticket}).status_code == 401
    with api(live, "akshat") as akshat:
        ticket = akshat.post("/api/auth/download-ticket").json()["ticket"]
    assert httpx.put(url, params={"ticket": ticket}, content=data).status_code == 401


def test_flow_04_fixtures_page_caps_page_size(live):
    with api(live, "alice") as alice:
        resp = alice.get("/api/fixtures", params={"workspace": "dev", "limit": 5000})
        assert resp.status_code == 200 and "total" in resp.json()
