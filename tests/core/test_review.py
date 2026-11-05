from datetime import UTC, datetime

import pytest

from evalkit.core.review import (
    CaseComment,
    CaseEvent,
    CaseEventKind,
    CaseReview,
    CaseState,
    group_threads,
    review_summary,
)


def ev(kind, hash_="h1", actor="akshat", target=None, ts=1):
    return CaseEvent(
        id=ts,
        dataset="finance",
        case_id="c1",
        content_hash=hash_,
        kind=kind,
        actor=actor,
        target=target,
        created_at=datetime(2026, 9, 22, 0, 0, ts, tzinfo=UTC),
    )


def review(last=None, requested_to=None, hash_="h1"):
    return CaseReview(
        dataset="finance",
        case_id="c1",
        content_hash=hash_,
        last_event=last,
        requested_to=requested_to,
    )


def test_state_is_draft_without_decisive_event():
    assert review().state() is CaseState.UNREVIEWED


@pytest.mark.parametrize(
    "kind,state",
    [
        (CaseEventKind.REVIEW_REQUESTED, CaseState.REVIEW_REQUESTED),
        (CaseEventKind.APPROVED, CaseState.APPROVED),
        (CaseEventKind.CHANGES_REQUESTED, CaseState.CHANGES_REQUESTED),
    ],
)
def test_state_follows_latest_decisive_kind(kind, state):
    assert review(last=ev(kind)).state() is state


def test_events_are_stamped_with_dataset_case_and_hash():
    r = review(hash_="abc")
    for event in (
        r.synced("ci@x"),
        r.request_review("akshat", "sme"),
        r.approve("sme"),
        r.request_changes("sme"),
    ):
        assert (event.dataset, event.case_id, event.content_hash) == ("finance", "c1", "abc")
    assert r.request_review("akshat", "sme").target == "sme"
    assert r.approve("sme").kind is CaseEventKind.APPROVED
    assert r.synced("ci@x").actor == "ci@x"


def test_carry_over_from_review_requested_keeps_target():
    r = review(last=ev(CaseEventKind.REVIEW_REQUESTED, target="sme"), requested_to="sme")
    carried = r.carry_over("akshat", "h2")
    assert carried is not None
    assert carried.kind is CaseEventKind.REVIEW_REQUESTED
    assert carried.content_hash == "h2"
    assert carried.target == "sme"
    assert carried.actor == "akshat"


def test_carry_over_from_changes_requested_falls_back_to_reviewer_who_acted():
    r = review(last=ev(CaseEventKind.CHANGES_REQUESTED, actor="sme"), requested_to=None)
    carried = r.carry_over("akshat", "h2")
    assert carried is not None and carried.target == "sme"


@pytest.mark.parametrize("last", [None, ev(CaseEventKind.APPROVED, actor="sme")])
def test_no_carry_over_from_draft_or_approved(last):
    assert review(last=last).carry_over("akshat", "h2") is None


def test_review_summary_shape():
    r = review(last=ev(CaseEventKind.REVIEW_REQUESTED, target="sme", ts=5), requested_to="sme")
    assert review_summary(r) == {
        "state": "review_requested",
        "requested_to": "sme",
        "updated_at": datetime(2026, 9, 22, 0, 0, 5, tzinfo=UTC),
    }
    assert review_summary(review()) == {
        "state": "unreviewed",
        "requested_to": None,
        "updated_at": None,
    }


def cm(id_, parent_id=None, resolved=False):
    return CaseComment(
        id=id_,
        dataset="finance",
        case_id="c1",
        content_hash="h1",
        parent_id=parent_id,
        author="sme",
        body=f"body {id_}",
        resolved=resolved,
        created_at=datetime(2026, 9, 22, 0, 0, id_, tzinfo=UTC),
    )


def test_group_threads_nests_replies_under_roots_oldest_first():
    threads = group_threads([cm(3, parent_id=1), cm(2), cm(1), cm(4, parent_id=2, resolved=True)])
    assert [t["id"] for t in threads] == [1, 2]
    assert [r["id"] for r in threads[0]["replies"]] == [3]
    assert threads[1]["replies"][0]["resolved"] is True
    assert "replies" not in threads[1]["replies"][0]
