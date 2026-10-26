import asyncio

import pytest

from evalkit.core.review import CaseEventKind, CaseReview, CaseState, CaseVersion, NewCaseComment
from evalkit.db.review import ReviewLog
from tests.seed import World, incoming, seed_world, sync

V1 = incoming("c1", question="first")
V2 = incoming("c1", question="second")
H1, H2 = V1.content_hash, V2.content_hash


@pytest.fixture
async def world(session_factory) -> World:
    world = await seed_world(session_factory)
    async with session_factory() as s:
        for workspace in (world.canonical, world.dev):
            await sync(s, workspace, world.admin_id, [V1])
            await sync(s, workspace, world.admin_id, [V2])
            await sync(s, workspace, world.admin_id, [V1])
        await s.commit()
    return world


@pytest.fixture
def log(session_factory, world) -> ReviewLog:
    return ReviewLog(session_factory)


async def test_load_returns_draft_without_decisive_events(log):
    loaded = await log.load("finance@canonical", [CaseVersion("c1", H1), CaseVersion("c9", "h9")])
    assert loaded["c1"].state() is CaseState.DRAFT
    assert loaded["c9"].state() is CaseState.DRAFT
    assert loaded["c1"].content_hash == H1
    assert await log.load("finance@canonical", []) == {}


async def test_draft_to_requested_to_approved(log):
    r = CaseReview.unreviewed("finance@canonical", "c1", H1)
    await log.append([r.request_review("admin", "akshat")])
    requested = (await log.load("finance@canonical", [CaseVersion("c1", H1)]))["c1"]
    assert requested.state() is CaseState.REVIEW_REQUESTED
    assert requested.requested_to == "akshat"
    await asyncio.sleep(0.01)
    await log.append([r.approve("akshat")])
    approved = (await log.load("finance@canonical", [CaseVersion("c1", H1)]))["c1"]
    assert approved.state() is CaseState.APPROVED
    assert approved.last_event is not None and approved.last_event.actor == "akshat"


async def test_load_latest_decisive_wins_and_ignores_other_hashes(log):
    r = CaseReview.unreviewed("finance@canonical", "c1", H1)
    await log.append([r.request_review("admin", "akshat")])
    await asyncio.sleep(0.01)
    await log.append([r.approve("akshat")])
    await asyncio.sleep(0.01)
    await log.append([r.request_changes("akshat")])
    await log.append([CaseReview.unreviewed("finance@canonical", "c1", H2).approve("akshat")])

    at_h1 = (await log.load("finance@canonical", [CaseVersion("c1", H1)]))["c1"]
    assert at_h1.state() is CaseState.CHANGES_REQUESTED
    assert at_h1.requested_to == "akshat"
    at_h2 = (await log.load("finance@canonical", [CaseVersion("c1", H2)]))["c1"]
    assert at_h2.state() is CaseState.APPROVED


async def test_same_transaction_ties_break_on_id(log):
    r = CaseReview.unreviewed("finance@canonical", "c1", H1)
    await log.append([r.approve("akshat"), r.request_changes("akshat")])
    loaded = await log.load("finance@canonical", [CaseVersion("c1", H1)])
    assert loaded["c1"].state() is CaseState.CHANGES_REQUESTED


async def test_review_state_is_per_dataset(log):
    await log.append([CaseReview.unreviewed("finance@canonical", "c1", H1).approve("akshat")])
    dev = await log.load("finance@dev", [CaseVersion("c1", H1)])
    assert dev["c1"].state() is CaseState.DRAFT


async def test_history_spans_versions_newest_first(log):
    events = await log.history("finance@canonical", "c1")
    assert [(e.kind, e.content_hash) for e in events] == [
        (CaseEventKind.SYNCED, H1),
        (CaseEventKind.SYNCED, H2),
        (CaseEventKind.SYNCED, H1),
    ]
    assert events[0].actor == "admin" and events[0].dataset == "finance@canonical"


async def test_comments_roundtrip_and_resolve(log):
    root = await log.add_comment(
        NewCaseComment(
            dataset="finance@dev", case_id="c1", content_hash=H1, author="akshat", body="fix"
        )
    )
    reply = await log.add_comment(
        NewCaseComment(
            dataset="finance@dev",
            case_id="c1",
            content_hash=H2,
            parent_id=root.id,
            author="admin",
            body="done",
        )
    )
    assert reply.parent_id == root.id and reply.author == "admin"
    assert [c.body for c in await log.comments("finance@dev", "c1")] == ["fix", "done"]
    assert await log.comments("finance@canonical", "c1") == []
    resolved = await log.set_resolved(root.id, True)
    assert (resolved.resolved, resolved.dataset, resolved.case_id) == (True, "finance@dev", "c1")
    fetched = await log.get_comment(root.id)
    assert fetched is not None and fetched.resolved is True
    assert await log.get_comment(9999) is None


async def test_decide_writes_comment_and_event_together(log):
    review = CaseReview.unreviewed("finance@canonical", "c1", H1)
    await log.decide(
        review.request_changes("akshat"),
        NewCaseComment(
            dataset="finance@canonical",
            case_id="c1",
            content_hash=H1,
            author="akshat",
            body="anchor",
        ),
    )
    loaded = await log.load("finance@canonical", [CaseVersion("c1", H1)])
    assert loaded["c1"].state() is CaseState.CHANGES_REQUESTED
    assert [c.body for c in await log.comments("finance@canonical", "c1")] == ["anchor"]


async def test_latest_hashes_returns_current_hash_per_case(log):
    assert await log.latest_hashes("finance@canonical", ["c1", "c3"]) == {"c1": H1}
    assert await log.latest_hashes("finance@canonical", []) == {}
