from collections.abc import Mapping
from dataclasses import dataclass

from evalkit.contracts.enums import WorkspaceRole


@dataclass(frozen=True)
class WorkspaceRef:
    id: int
    name: str
    owner_user_id: int | None
    archived: bool
    promotes_to: int | None = None
    promoted_from: int | None = None

    @property
    def is_shared(self) -> bool:
        return self.owner_user_id is None

    @property
    def is_target(self) -> bool:
        return self.promoted_from is not None


@dataclass(frozen=True)
class Actor:
    id: int
    username: str
    is_admin: bool
    roles: Mapping[int, frozenset[WorkspaceRole]]

    def has(self, ws: WorkspaceRef, role: WorkspaceRole) -> bool:
        return role in self.roles.get(ws.id, frozenset())


def can_read(actor: Actor, ws: WorkspaceRef) -> bool:
    return True


def can_write(actor: Actor, ws: WorkspaceRef) -> bool:
    if not ws.is_shared:
        return ws.owner_user_id == actor.id
    return actor.is_admin or actor.has(ws, WorkspaceRole.WRITER)


def can_set_endpoint(actor: Actor, ws: WorkspaceRef) -> bool:
    return can_write(actor, ws)


def can_manage_llm_providers(actor: Actor) -> bool:
    return actor.is_admin


def can_review(actor: Actor, ws: WorkspaceRef) -> bool:
    return ws.is_shared and actor.has(ws, WorkspaceRole.REVIEWER)


def can_use_agent(actor: Actor, agent_ws: WorkspaceRef) -> bool:
    if not agent_ws.is_shared:
        return agent_ws.owner_user_id == actor.id
    return True


def can_change_experiment(actor: Actor, exp_ws: WorkspaceRef, created_by: int | None) -> bool:
    return can_write(actor, exp_ws) or created_by == actor.id


def can_create_experiment(actor: Actor, exp_ws: WorkspaceRef) -> bool:
    if not exp_ws.is_shared:
        return exp_ws.owner_user_id == actor.id
    return (
        actor.is_admin
        or actor.has(exp_ws, WorkspaceRole.WRITER)
        or actor.has(exp_ws, WorkspaceRole.REVIEWER)
    )


def experiment_workspace(
    runner_personal: WorkspaceRef, dataset_ws: WorkspaceRef, agent_ws: WorkspaceRef
) -> WorkspaceRef:
    if dataset_ws.is_shared and agent_ws.is_shared:
        return dataset_ws
    return runner_personal
