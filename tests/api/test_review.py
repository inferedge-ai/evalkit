import httpx
import pytest

from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.review import CaseEventKind, CaseReview, CaseState, CaseVersion
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_harness import FakeHarness
from tests.native import USERS, seed_env, serve

FINANCE = "finance@canonical"
BASE = f"/api/datasets/{FINANCE}"

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
    users = UserStore(session_factory)
    for name in ("sme", "other"):
        await users.create(name, "password")
    workspaces = WorkspaceStore(session_factory)
    await workspaces.add_member(
        "canonical", "sme", WorkspaceRole.REVIEWER, granted_by=env.user_ids["admin"]
    )
    await workspaces.remove_member("canonical", "admin", WorkspaceRole.REVIEWER)
    async with serve(env, tmp_path) as admin:
        clients = {"admin": admin}
        for name in ("alice", "sme"):
            client = httpx.AsyncClient(transport=admin._transport, base_url="http://test")
            password = USERS.get(name, "password")
            resp = await client.post(
                "/api/auth/login", json={"username": name, "password": password}
            )
            client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
            clients[name] = client
        hashes = {
            c["case_id"]: c["content_hash"] for c in (await admin.get(f"{BASE}/cases")).json()
        }
        yield clients, env, hashes
        for name in ("alice", "sme"):
            await clients[name].aclose()


def upload(name="cases.yaml", body=CASES_YAML):
    return [("files", (name, body, "application/octet-stream"))]


async def test_sync_records_actor_from_principal(env):
    clients, ctx, _ = env
    resp = await clients["admin"].post(
        "/api/sync", params={"dataset": "finance", "workspace": "canonical"}, files=upload()
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["review"] == {"now_unreviewed": [], "carried": []}
    synced = [
        e for e in await ctx.review.history("finance@canonical", "case-1") if e.kind == "synced"
    ]
    assert len(synced) == 2 and all(e.actor == "admin" for e in synced)


async def test_me_roles_and_reviewers_endpoint(env):
    clients, *_ = env
    assert (await clients["sme"].get("/api/auth/me")).json()["roles"] == {"canonical": ["reviewer"]}
    assert (await clients["alice"].get("/api/auth/me")).json()["roles"] == {}
    reviewers = await clients["alice"].get(
        "/api/workspaces/canonical/members", params={"role": "reviewer"}
    )
    assert [m["username"] for m in reviewers.json()] == ["sme"]
    assert (await clients["alice"].get("/api/auth/reviewers")).status_code == 404


async def test_cases_list_carries_review_state(env):
    clients, ctx, hashes = env
    await ctx.review.append(
        [CaseReview.unreviewed("finance@canonical", "case-1", hashes["case-1"]).approve("sme")]
    )
    rows = (await clients["alice"].get(f"{BASE}/cases")).json()
    by_id = {r["case_id"]: r["review"] for r in rows}
    assert by_id["case-1"]["state"] == "approved"
    assert by_id["case-2"] == {"state": "unreviewed", "requested_to": None, "updated_at": None}
    assert {r["case_id"]: r["review_state"] for r in rows} == {
        "case-1": "approved",
        "case-2": "unreviewed",
    }


async def test_request_review_by_selection(env):
    clients, ctx, hashes = env
    resp = await clients["alice"].post(
        f"{BASE}/review-requests",
        json={"selection": {"case_ids": ["case-1"]}, "reviewer": "sme"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"requested": ["case-1"]}
    loaded = await ctx.review.load(
        "finance@canonical",
        [CaseVersion("case-1", hashes["case-1"]), CaseVersion("case-2", hashes["case-2"])],
    )
    assert loaded["case-1"].state() is CaseState.REVIEW_REQUESTED
    assert loaded["case-1"].requested_to == "sme"
    assert loaded["case-1"].last_event.actor == "alice"
    assert loaded["case-2"].state() is CaseState.UNREVIEWED

    bad = await clients["alice"].post(
        f"{BASE}/review-requests",
        json={"selection": {"all": True}, "reviewer": "nobody"},
    )
    assert bad.status_code == 400


async def test_decisions_are_reviewer_only_and_write_comment_with_event(env):
    clients, ctx, hashes = env
    denied = await clients["alice"].post(f"{BASE}/cases/case-1/review", json={"action": "approve"})
    assert denied.status_code == 403

    missing = await clients["sme"].post(
        f"{BASE}/cases/case-1/review", json={"action": "request_changes"}
    )
    assert missing.status_code == 400

    resp = await clients["sme"].post(
        f"{BASE}/cases/case-1/review",
        json={"action": "request_changes", "comment": "anchor the date"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "changes_requested"
    comments = await ctx.review.comments("finance@canonical", "case-1")
    assert [(c.body, c.author, c.content_hash, c.parent_id) for c in comments] == [
        ("anchor the date", "sme", hashes["case-1"], None)
    ]

    approved = await clients["sme"].post(f"{BASE}/cases/case-1/review", json={"action": "approve"})
    assert approved.json()["state"] == "approved"
    unknown = await clients["sme"].post(f"{BASE}/cases/nope/review", json={"action": "approve"})
    assert unknown.status_code == 404


async def test_events_endpoint_lists_history_newest_first(env):
    clients, ctx, hashes = env
    r = CaseReview.unreviewed("finance@canonical", "case-1", hashes["case-1"])
    await ctx.review.append([r.request_review("alice", "sme")])
    resp = await clients["alice"].get(f"{BASE}/cases/case-1/events")
    events = resp.json()["events"]
    assert [e["kind"] for e in events] == [
        CaseEventKind.REVIEW_REQUESTED.value,
        CaseEventKind.SYNCED.value,
    ]
    assert events[0]["target"] == "sme" and events[0]["content_hash"] == hashes["case-1"]
    assert events[1]["actor"] == "admin"


async def test_comments_threads_and_resolve_permissions(env):
    clients, *_ = env
    base = f"{BASE}/cases/case-1/comments"
    root = await clients["alice"].post(base, json={"body": "why 2021?"})
    assert root.status_code == 200
    root_id = root.json()["id"]
    reply = await clients["sme"].post(base, json={"body": "because FY", "parent_id": root_id})
    assert reply.status_code == 200
    nested = await clients["sme"].post(base, json={"body": "nope", "parent_id": reply.json()["id"]})
    assert nested.status_code == 400
    wrong_case = await clients["sme"].post(
        f"{BASE}/cases/case-2/comments",
        json={"body": "nope", "parent_id": root_id},
    )
    assert wrong_case.status_code == 400

    threads = (await clients["alice"].get(base)).json()["threads"]
    assert len(threads) == 1 and threads[0]["id"] == root_id
    assert [r["body"] for r in threads[0]["replies"]] == ["because FY"]

    path = f"{base}/{root_id}"
    assert (await clients["admin"].patch(path, json={"resolved": True})).status_code == 403
    assert (await clients["alice"].patch(path, json={"resolved": True})).json()["resolved"] is True
    assert (await clients["sme"].patch(path, json={"resolved": False})).json()["resolved"] is False
    missing = await clients["sme"].patch(f"{base}/9999", json={"resolved": True})
    assert missing.status_code == 404


async def test_queue_filters_by_reviewer_and_current_state(env):
    clients, ctx, hashes = env
    await clients["alice"].post(
        f"{BASE}/review-requests",
        json={"selection": {"case_ids": ["case-1"]}, "reviewer": "sme"},
    )
    await ctx.review.append(
        [
            CaseReview.unreviewed("finance@canonical", "case-2", hashes["case-2"]).request_review(
                "alice", "other"
            )
        ]
    )
    mine = (await clients["sme"].get("/api/review/queue", params={"reviewer": "sme"})).json()
    assert [(i["dataset"], i["case_id"], i["requested_by"], i["input"]) for i in mine["items"]] == [
        (FINANCE, "case-1", "alice", "q1")
    ]
    everyone = (await clients["sme"].get("/api/review/queue")).json()["items"]
    assert {i["case_id"] for i in everyone} == {"case-1", "case-2"}
    in_canonical = (
        await clients["sme"].get("/api/review/queue", params={"workspace": "canonical"})
    ).json()["items"]
    assert {i["case_id"] for i in in_canonical} == {"case-1", "case-2"}
    elsewhere = (
        await clients["sme"].get("/api/review/queue", params={"workspace": "alice"})
    ).json()["items"]
    assert elsewhere == []

    await clients["sme"].post(f"{BASE}/cases/case-1/review", json={"action": "approve"})
    after = (await clients["sme"].get("/api/review/queue", params={"reviewer": "sme"})).json()
    assert after["items"] == []


async def test_queue_can_list_changes_requested(env):
    clients, *_ = env
    await clients["sme"].post(
        f"{BASE}/cases/case-1/review",
        json={"action": "request_changes", "comment": "anchor the date"},
    )
    await clients["alice"].post(
        f"{BASE}/review-requests",
        json={"selection": {"case_ids": ["case-2"]}, "reviewer": "sme"},
    )
    changes = (
        await clients["alice"].get("/api/review/queue", params={"state": "changes_requested"})
    ).json()
    assert [(i["case_id"], i["requested_by"], i["requested_to"]) for i in changes["items"]] == [
        ("case-1", "sme", None)
    ]
    waiting = (await clients["alice"].get("/api/review/queue")).json()
    assert [i["case_id"] for i in waiting["items"]] == ["case-2"]
    assert (
        await clients["alice"].get("/api/review/queue", params={"state": "unreviewed"})
    ).status_code == 422
