import httpx2
import pytest
from sqlalchemy import func, select

from evalkit.api import review as review_api
from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.dataset_readme import DatasetMetadata, DatasetReadme
from evalkit.db.cases import find_dataset_row, set_dataset_readme
from evalkit.db.models import CaseEvent, DatasetSync
from evalkit.db.promotion import plan_promotion
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
    await UserStore(session_factory, "default").create("sme", password="password")
    workspaces = WorkspaceStore(session_factory, "default")
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
            client = httpx2.AsyncClient(transport=admin._transport, base_url="http://test")
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
    assert resp.json()["state"] == "approved"
    assert resp.json()["promoted_to"] == {"target": PROD, "cases": ["case-1"]}

    dev = await _cases(clients["sme"], DEV)
    prod = await _cases(clients["sme"], PROD)
    assert list(prod) == ["case-1"]
    assert prod["case-1"]["content_hash"] == dev["case-1"]["content_hash"]
    assert prod["case-1"]["review"]["state"] == "approved"
    assert prod["case-1"]["review"]["updated_at"] is not None
    events = (await clients["sme"].get(f"/api/datasets/{PROD}/cases/case-1/events")).json()
    prod_events = [e for e in events["events"] if e["dataset"] == PROD]
    assert [(e["kind"], e["actor"]) for e in prod_events] == [
        ("approved", "sme"),
        ("synced", "sme"),
    ]
    dev_events = [(e["kind"], e["dataset"]) for e in events["events"] if e["dataset"] == DEV]
    assert ("approved", DEV) in dev_events and ("synced", DEV) in dev_events
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
    events = (await clients["sme"].get(f"/api/datasets/{PROD}/cases/case-1/events")).json()
    assert {e["content_hash"] for e in events["events"]} == {first}

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
    assert "case-1" not in await _cases(sme, PROD)
    assert (await _cases(sme, "finance@canonical"))["case-1"]["twin"] is None


async def test_a_promoted_dataset_takes_the_readme_and_metadata_of_its_source(env):
    clients, ctx = env
    workspace = await WorkspaceStore(ctx.session_factory, "default").get_by_name("dev")
    async with ctx.session_factory() as session:
        dataset = await find_dataset_row(session, "default", "dev", "finance")
        assert dataset is not None
        await set_dataset_readme(
            session,
            dataset.id,
            DatasetReadme(readme="# Finance\n", metadata=DatasetMetadata(description="Filings.")),
        )
        await session.commit()
    assert workspace.promotes_to is not None
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})

    resp = await _decide(clients["sme"], DEV, "case-1", "approve")

    assert resp.status_code == 200, resp.text
    promoted = (await clients["sme"].get(f"/api/datasets/{PROD}")).json()
    assert (promoted["readme"], promoted["metadata"]) == (
        "# Finance\n",
        {"description": "Filings."},
    )


async def _latest_sync_detail(ctx, ref):
    workspace, name = ref.split("@")[1], ref.split("@")[0]
    async with ctx.session_factory() as session:
        dataset = await find_dataset_row(session, "default", workspace, name)
        assert dataset is not None
        row = await session.scalar(
            select(DatasetSync)
            .where(DatasetSync.dataset_id == dataset.id)
            .order_by(DatasetSync.id.desc())
            .limit(1)
        )
    assert row is not None
    return row.source, row.detail


async def test_removing_a_case_from_the_source_removes_it_from_the_target(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200

    result = await sync_cases(ctx, [suite_case("case-2", "q2")], workspace="dev", full=True)

    assert result.removed == ["case-1"]
    assert result.removed_from is not None
    assert (result.removed_from.target, result.removed_from.cases) == (PROD, ["case-1"])
    assert "case-1" not in await _cases(clients["sme"], PROD)
    assert await _latest_sync_detail(ctx, PROD) == (
        "promotion",
        {"from": DEV, "removed": ["case-1"], "removal_reason": "removed_from_source"},
    )


async def test_removing_an_unpromoted_case_leaves_the_target_alone(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})

    result = await sync_cases(ctx, [suite_case("case-2", "q2")], workspace="dev", full=True)

    assert result.removed == ["case-1"] and result.removed_from is None


async def test_changes_requested_on_the_promoted_version_removes_it_from_the_target(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200

    resp = await _decide(clients["sme"], DEV, "case-1", "request_changes", "wrong date")

    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_from"] == {"target": PROD, "cases": ["case-1"]}
    assert "case-1" not in await _cases(clients["sme"], PROD)
    source, detail = await _latest_sync_detail(ctx, PROD)
    assert source == "promotion" and isinstance(detail.pop("event_id"), int)
    assert detail == {"from": DEV, "removed": ["case-1"], "removal_reason": "changes_requested"}

    resp = await _decide(clients["sme"], DEV, "case-1", "approve")
    assert resp.json()["promoted_to"] == {"target": PROD, "cases": ["case-1"]}
    assert (await _cases(clients["sme"], PROD))["case-1"]["review"]["state"] == "approved"


async def test_changes_requested_on_a_newer_version_leaves_the_target_alone(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200
    await sync_cases(ctx, [suite_case("case-1", "q1 v2")], workspace="dev")

    resp = await _decide(clients["sme"], DEV, "case-1", "request_changes", "still wrong")

    assert resp.json()["removed_from"] is None
    assert (await _cases(clients["sme"], PROD))["case-1"]["review"]["state"] == "approved"


async def test_a_case_that_comes_back_approved_returns_to_the_target(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200
    both = [suite_case("case-1", "q1"), suite_case("case-2", "q2")]
    await sync_cases(ctx, both[1:], workspace="dev", full=True)

    result = await sync_cases(ctx, both, workspace="dev", full=True)

    assert result.promoted_to is not None
    assert (result.promoted_to.target, result.promoted_to.cases) == (PROD, ["case-1"])
    assert (await _cases(clients["sme"], PROD))["case-1"]["review"]["state"] == "approved"


async def test_a_case_sent_back_stays_out_of_the_target_when_it_comes_back(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200
    await _decide(clients["sme"], DEV, "case-1", "request_changes", "wrong date")
    both = [suite_case("case-1", "q1"), suite_case("case-2", "q2")]
    await sync_cases(ctx, both[1:], workspace="dev", full=True)

    result = await sync_cases(ctx, both, workspace="dev", full=True)

    assert result.promoted_to is None
    assert "case-1" not in await _cases(clients["sme"], PROD)


async def test_a_case_that_comes_back_keeps_its_approver_in_the_target(env):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})
    assert (await _decide(clients["sme"], DEV, "case-1", "approve")).status_code == 200
    both = [suite_case("case-1", "q1"), suite_case("case-2", "q2")]
    await sync_cases(ctx, both[1:], workspace="dev", full=True, actor="alice")
    await sync_cases(ctx, both, workspace="dev", full=True, actor="alice")

    found = (await clients["sme"].get(f"/api/datasets/{PROD}/cases/case-1/events")).json()
    approvals = [e for e in found["events"] if e["dataset"] == PROD and e["kind"] == "approved"]
    assert [e["actor"] for e in approvals] == ["sme", "sme"]


async def test_an_approval_racing_a_removal_answers_409_and_rolls_back(env, monkeypatch):
    clients, ctx = env
    await clients["admin"].post("/api/datasets", json={"name": "finance", "workspace": "prod"})

    async def plan_then_remove(session, ws, dataset, case):
        plan = await plan_promotion(session, ws, dataset, case)
        await sync_cases(ctx, [suite_case("case-2", "q2")], workspace="dev", full=True)
        return plan

    monkeypatch.setattr(review_api, "plan_promotion", plan_then_remove)
    resp = await _decide(clients["sme"], DEV, "case-1", "approve")

    assert resp.status_code == 409 and "changed or was removed" in resp.json()["detail"]
    assert "case-1" not in await _cases(clients["sme"], PROD)
    async with ctx.session_factory() as session:
        approvals = await session.scalar(
            select(func.count()).select_from(CaseEvent).where(CaseEvent.kind == "approved")
        )
    assert approvals == 0


async def test_sync_refuses_under_with_prune(env):
    clients, _ = env
    resp = await clients["admin"].post(
        "/api/sync",
        params={"dataset": "finance", "workspace": "dev", "prune": True, "under": "a"},
        files=[("files", ("a/c.yaml", CASES_YAML))],
    )
    assert resp.status_code == 400 and "prune=false" in resp.json()["detail"]
