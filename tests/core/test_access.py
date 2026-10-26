from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.access import (
    Actor,
    WorkspaceRef,
    can_change_experiment,
    can_read,
    can_review,
    can_use_agent,
    can_write,
    experiment_workspace,
)

AKSHAT = WorkspaceRef(1, "akshat", owner_user_id=10, archived=False)
BOB_WS = WorkspaceRef(2, "bob", owner_user_id=20, archived=False)
DEV = WorkspaceRef(3, "dev", owner_user_id=None, archived=False)
PROD = WorkspaceRef(4, "prod", owner_user_id=None, archived=False)


def actor(uid, admin=False):
    return Actor(uid, f"u{uid}", admin, {})


def test_everyone_reads_everything():
    assert can_read(actor(20), AKSHAT) and can_read(actor(20), DEV)


def test_personal_write_is_owner_only_even_for_admin():
    assert can_write(actor(10), AKSHAT)
    assert not can_write(actor(99, admin=True), AKSHAT)


def test_shared_write_admin_or_writer():
    assert can_write(actor(99, admin=True), DEV)
    assert can_write(Actor(20, "bob", False, {3: frozenset({WorkspaceRole.WRITER})}), DEV)
    assert not can_write(actor(20), DEV)


def test_review_needs_reviewer_role_on_shared():
    reviewer = Actor(20, "bob", False, {3: frozenset({WorkspaceRole.REVIEWER})})
    assert can_review(reviewer, DEV) and not can_review(reviewer, PROD)
    assert not can_review(actor(99, admin=True), DEV)


def test_use_agent():
    assert can_use_agent(actor(10), AKSHAT) and not can_use_agent(actor(20), AKSHAT)
    assert can_use_agent(actor(20), DEV)


def test_change_experiment_writer_or_creator():
    assert can_change_experiment(actor(20), DEV, created_by=20)
    assert not can_change_experiment(actor(30), DEV, created_by=20)
    assert can_change_experiment(actor(99, admin=True), DEV, created_by=20)
    assert not can_change_experiment(actor(99, admin=True), AKSHAT, created_by=10)


def test_experiment_home_rule():
    assert experiment_workspace(AKSHAT, DEV, DEV) == DEV
    assert experiment_workspace(AKSHAT, PROD, AKSHAT) == AKSHAT
    assert experiment_workspace(AKSHAT, AKSHAT, DEV) == AKSHAT
    assert experiment_workspace(AKSHAT, BOB_WS, DEV) == AKSHAT
