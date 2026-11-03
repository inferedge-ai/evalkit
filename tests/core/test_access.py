from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.access import (
    Actor,
    WorkspaceRef,
    can_change_experiment,
    can_create_experiment,
    can_read,
    can_review,
    can_use_agent,
    can_write,
    experiment_workspace,
    same_tenant,
)

AKSHAT = WorkspaceRef(1, "akshat", owner_user_id=10, archived=False, tenant_id=1)
BOB_WS = WorkspaceRef(2, "bob", owner_user_id=20, archived=False, tenant_id=1)
DEV = WorkspaceRef(3, "dev", owner_user_id=None, archived=False, tenant_id=1)
PROD = WorkspaceRef(4, "prod", owner_user_id=None, archived=False, tenant_id=1)
OTHER_DEV = WorkspaceRef(5, "dev", owner_user_id=None, archived=False, tenant_id=2)


def actor(uid, admin=False, tenant_id=1):
    return Actor(uid, f"u{uid}", admin, tenant_id, {})


def test_everyone_in_the_tenant_reads_everything():
    assert can_read(actor(20), AKSHAT) and can_read(actor(20), DEV)


def test_personal_write_is_owner_only_even_for_admin():
    assert can_write(actor(10), AKSHAT)
    assert not can_write(actor(99, admin=True), AKSHAT)


def test_shared_write_admin_or_writer():
    assert can_write(actor(99, admin=True), DEV)
    assert can_write(Actor(20, "bob", False, 1, {3: frozenset({WorkspaceRole.WRITER})}), DEV)
    assert not can_write(actor(20), DEV)


def test_review_needs_reviewer_role_on_shared():
    reviewer = Actor(20, "bob", False, 1, {3: frozenset({WorkspaceRole.REVIEWER})})
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


def test_every_rule_refuses_a_workspace_in_another_tenant():
    admin = actor(99, admin=True)
    writer = Actor(
        20, "bob", False, 1, {5: frozenset({WorkspaceRole.WRITER, WorkspaceRole.REVIEWER})}
    )
    assert not can_read(admin, OTHER_DEV)
    assert not can_write(admin, OTHER_DEV)
    assert not can_write(writer, OTHER_DEV)
    assert not can_review(writer, OTHER_DEV)
    assert not can_use_agent(admin, OTHER_DEV)
    assert not can_create_experiment(writer, OTHER_DEV)
    assert not can_change_experiment(admin, OTHER_DEV, created_by=99)


def test_same_tenant():
    assert same_tenant(actor(1), DEV)
    assert not same_tenant(actor(1, tenant_id=2), DEV)
