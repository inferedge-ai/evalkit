from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import EvaluatorKind, TrajectorySource, WorkspaceRole
from evalkit.contracts.settings import AgentSettings, JudgeSettings
from evalkit.core.agents import AgentDefinition
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import (
    FAKE_EVALUATORS,
    FAKE_HARNESS_URL,
    FAKE_SETTINGS,
    JUDGE_URL,
    TEST_BOX,
    TEST_ENV,
)


async def seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
    users: dict[str, str],
    *,
    admins: Iterable[str] = ("admin",),
    shared: Iterable[str] = ("canonical",),
    reviewers: Iterable[str] = (),
    writers: Iterable[str] = (),
) -> None:
    admins = set(admins)
    store = UserStore(session_factory)
    ids = {}
    for name, password in users.items():
        ids[name] = (await store.create(name, password, is_admin=name in admins)).id
    workspaces = WorkspaceStore(session_factory)
    granter = next(ids[name] for name in users if name in admins)
    for name in shared:
        await workspaces.create_shared(name, granter)
        for user in reviewers:
            await workspaces.add_member(name, user, WorkspaceRole.REVIEWER, granter)
        for user in writers:
            await workspaces.add_member(name, user, WorkspaceRole.WRITER, granter)
    providers = LlmProviderStore(session_factory)
    await providers.create("judge", JUDGE_URL, TEST_BOX.seal(TEST_ENV["JUDGE_KEY"]))
    await providers.set_default_judge(JudgeSettings(base_url=JUDGE_URL, model="judge-model"))


async def seed_agent(
    session_factory: async_sessionmaker[AsyncSession],
    name: str = "fake",
    workspace: str = "canonical",
    *,
    harness_url: str | None = FAKE_HARNESS_URL,
    trajectory_source: TrajectorySource = TrajectorySource.ATIF,
    settings: AgentSettings = FAKE_SETTINGS,
    mapping: dict | None = None,
) -> AgentDefinition:
    return await AgentStore(session_factory).create(
        workspace,
        name,
        harness_url=harness_url,
        trajectory_source=trajectory_source,
        settings=settings,
        mapping=mapping,
        api_key_encrypted=TEST_BOX.seal(TEST_ENV["FAKE_AGENT_KEY"]) if harness_url else None,
    )


async def seed_evaluators(
    session_factory: async_sessionmaker[AsyncSession], workspace: str = "canonical"
) -> None:
    store = RemoteEvaluatorStore(session_factory)
    for name, settings in FAKE_EVALUATORS.items():
        await store.create(
            workspace,
            name,
            EvaluatorKind.SCORE,
            settings,
            TEST_BOX.seal(TEST_ENV["CUSTOM_EVAL_KEY"]),
        )
