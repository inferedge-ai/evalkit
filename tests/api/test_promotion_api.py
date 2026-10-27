import httpx
import pytest

from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_harness import FakeHarness
from tests.native import USERS, seed_env, serve, suite_case, sync_cases

DEV = "finance@dev"
PROD = "finance@prod"
CASES_YAML = b"""
cases:
- id: case-1
  tags: [finance]
  input: {question: "q1 edited"}
  evaluators:
    output:
      answer_rubrics:
        rubrics:
        - rubric: "States 17,133 stores at FY2021 year-end."
          type: factual_retrieval
"""


@pytest.fixture
async def env(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness(), approved=False)
    await UserStore(session_factory).create("sme", "password")
    workspaces = WorkspaceStore(session_factory)
    admin_id = env.user_ids["admin"]
    for name in ("dev", "prod"):
        await workspaces.create_shared(name, admin_id)
    await workspaces.add_member("dev", "sme", WorkspaceRole.REVIEWER, granted_by=admin_id)
    await workspaces.add_member("prod", "sme", WorkspaceRole.REVIEWER, granted_by=admin_id)
    await workspaces.add_member("dev", "alice", WorkspaceRole.WRITER, granted_by=admin_id)
    await workspaces.set_promotes_to("dev", "prod")
    await sync_cases(env, [suite_case("case-1", "q1"), suite_case("case-2", "q2")], workspace="dev")
    async with serve(env, tmp_path) as admin:
        clients = {"admin": admin}
        for name in ("alice", "sme"):
            client = httpx.AsyncClient(transport=admin._transport, base_url="http://test")
            resp = await client.post(
                "/api/auth/login", json={"username": name, "password": USERS.get(name, "password")}
            )
            client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
            clients[name] = client
        yield clients, env
        for name in ("alice", "sme"):
            await clients[name].aclose()


async def _cases(client, ref):
    return {c["case_id"]: c for c in (await client.get(f"/api/datasets/{ref}/cases")).json()}


async def _decide(client, ref, case_id, action, comment=None):
    return await client.post(
        f"/api/datasets/{ref}/cases/{case_id}/review",
        json={"action": action, "comment": comment},
    )


async def test_approve_promotes_and_rejection_does_not(env):
    clients, ctx = env
    resp = await _decide(clients["sme"], DEV, "case-1", "approve")
    assert resp.status_code == 409 and "finance@prod" in resp.json()["detail"]
    assert (await _cases(clients["sme"], DEV))["case-1"]["review"]["state"] == "unreviewed"

    assert (
        await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    ).status_code == 200
    resp = await _decide(clients["sme"], DEV, "case-1", "approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "approved" and resp.json()["promoted_to"] == PROD

    dev = await _cases(clients["sme"], DEV)
    prod = await _cases(clients["sme"], PROD)
    assert list(prod) == ["case-1"]
    assert prod["case-1"]["content_hash"] == dev["case-1"]["content_hash"]
    assert prod["case-1"]["review"]["state"] == "approved"
    assert prod["case-1"]["review"]["updated_at"] is not None
    events = (await clients["sme"].get(f"/api/datasets/{PROD}/cases/case-1/events")).json()
    assert [(e["kind"], e["actor"]) for e in events["events"]] == [
        ("approved", "sme"),
        ("synced", "sme"),
    ]
    last = (await clients["sme"].get(f"/api/datasets/{PROD}")).json()["last_sync"]
    assert last["source"] == "promotion" and last["detail"]["from"] == DEV

    resp = await _decide(clients["sme"], DEV, "case-2", "request_changes", "no")
    assert resp.status_code == 200 and resp.json()["promoted_to"] is None
    assert list(await _cases(clients["sme"], PROD)) == ["case-1"]


async def test_target_is_locked(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    await _decide(clients["sme"], DEV, "case-1", "approve")

    resp = await clients["admin"].post(
        "/api/sync",
        params={"dataset": "finance", "workspace": "prod"},
        files=[("files", ("cases.yaml", CASES_YAML, "application/octet-stream"))],
    )
    assert resp.status_code == 409 and "sync dev instead" in resp.json()["detail"]

    resp = await _decide(clients["sme"], PROD, "case-1", "request_changes", "hmm")
    assert resp.status_code == 409 and "review case-1 in finance@dev" in resp.json()["detail"]
    resp = await clients["sme"].post(
        f"/api/datasets/{PROD}/review-requests",
        json={"selection": {"case_ids": ["case-1"]}, "reviewer": "sme"},
    )
    assert resp.status_code == 409 and "finance@dev" in resp.json()["detail"]

    resp = await clients["alice"].post(
        f"/api/datasets/{PROD}/cases/case-1/comments", json={"body": "looks good"}
    )
    assert resp.status_code == 200, resp.text
    assert (await _cases(clients["sme"], PROD))["case-1"]["review"]["state"] == "approved"


async def test_prod_stays_on_last_approved_version(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    await _decide(clients["sme"], DEV, "case-1", "approve")
    first = (await _cases(clients["sme"], PROD))["case-1"]["content_hash"]

    await sync_cases(ctx, [suite_case("case-1", "q1 v2")], workspace="dev")
    dev = await _cases(clients["sme"], DEV)
    assert dev["case-1"]["review"]["state"] == "unreviewed"
    assert (await _cases(clients["sme"], PROD))["case-1"]["content_hash"] == first

    resp = await _decide(clients["sme"], DEV, "case-1", "approve")
    assert resp.status_code == 200
    prod = await _cases(clients["sme"], PROD)
    assert prod["case-1"]["content_hash"] == dev["case-1"]["content_hash"] != first
    assert prod["case-1"]["review"]["state"] == "approved"


async def test_twin_on_both_sides(env):
    clients, ctx = env
    sme = clients["sme"]
    dev = await _cases(sme, DEV)
    assert dev["case-1"]["twin"] == {
        "workspace": "prod",
        "state": "absent",
        "content_hash": None,
        "review": None,
        "overlays_differ": False,
        "href": "/datasets/finance@prod/cases/case-1",
    }
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    await _decide(sme, DEV, "case-1", "approve")

    one = await sme.get(f"/api/datasets/{DEV}/cases/case-1")
    assert one.status_code == 200 and one.json()["twin"]["state"] == "same"
    assert one.json()["twin"]["review"]["state"] == "approved"
    assert (await sme.get(f"/api/datasets/{DEV}/cases/nope")).status_code == 404

    back = (await sme.get(f"/api/datasets/{PROD}/cases/case-1")).json()["twin"]
    assert back["workspace"] == "dev" and back["state"] == "same"
    assert back["review"]["state"] == "approved"
    assert back["href"] == "/datasets/finance@dev/cases/case-1"

    await sync_cases(ctx, [suite_case("case-1", "q1 v2")], workspace="dev")
    assert (await _cases(sme, DEV))["case-1"]["twin"]["state"] == "differs"
    back = (await _cases(sme, PROD))["case-1"]["twin"]
    assert back["state"] == "differs" and back["review"]["state"] == "unreviewed"

    await sync_cases(ctx, [suite_case("case-2", "q2")], workspace="dev", full=True)
    assert (await _cases(sme, PROD))["case-1"]["twin"]["state"] == "absent"
    assert (await _cases(sme, "finance@canonical"))["case-1"]["twin"] is None
