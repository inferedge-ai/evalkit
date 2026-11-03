from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from evalkit.api.app import create_app
from evalkit.contracts.enums import TraceProvider
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.runner import RunnerDeps
from evalkit.db.agents import AgentStore
from evalkit.db.crypto import SecretBox
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.models import Tenant
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.store import PgStore
from evalkit.db.trace_imports import TraceImportStore
from evalkit.db.trace_sources import EncryptionKeyMissing, TraceSourceStore, UnknownTraceSource
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.native import make_cfg


def box() -> SecretBox:
    return SecretBox(Fernet.generate_key().decode())


def test_secret_box_round_trip():
    sealed = box()
    blob = sealed.seal("pk")
    assert blob != b"pk" and sealed.open(blob) == "pk"


async def test_keys_are_sealed_hidden_and_rekeyed(session_factory):
    old = box()
    store = TraceSourceStore(session_factory, 1, old)
    await store.create("phx", TraceProvider.PHOENIX, "https://p", "pk")
    listed = await store.list_sources()
    assert [s.name for s in listed] == ["phx"] and "api_key" not in listed[0].model_dump()
    resolved = await store.resolve("phx")
    assert resolved.api_key is not None and resolved.api_key.get_secret_value() == "pk"
    assert "pk" not in repr(resolved)


async def test_creating_without_a_box_is_refused(session_factory):
    with pytest.raises(EncryptionKeyMissing, match="encryption_key is not configured"):
        await TraceSourceStore(session_factory, 1, None).create(
            "phx", TraceProvider.PHOENIX, "u", "pk"
        )


async def test_server_refuses_to_start_with_sources_but_no_key(tmp_path, session_factory):
    await TraceSourceStore(session_factory, 1, box()).create(
        "phx", TraceProvider.PHOENIX, "u", "pk"
    )
    cfg = make_cfg(tmp_path).model_copy(update={"encryption_key": None})
    store = PgStore(session_factory, 1)
    app = create_app(
        cfg,
        runner_deps=RunnerDeps(
            cfg=cfg,
            store=store,
            blob_store=DictBlobStore(),
            agents=FakeAgents(),
            evaluators=FakeEvaluators(),
            credentials=fake_credentials(),
        ),
        session_factory=session_factory,
        check_schema=False,
    )
    with pytest.raises(RuntimeError, match="encryption_key"):
        async with app.router.lifespan_context(app):
            pass


async def test_default_judge_lives_on_the_tenant_row(session_factory):
    store = LlmProviderStore(session_factory, 1)
    await store.create("p", "https://llm.example", None)
    judge = JudgeSettings(base_url="https://llm.example", model="m")
    await store.set_default_judge(judge)
    assert await store.default_judge() == judge
    async with session_factory() as s:
        row = await s.get(Tenant, 1)
    assert row is not None and row.default_judge["model"] == "m"


async def test_trace_import_refuses_a_source_from_another_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    admin = await UserStore(raw_session_factory, 1).create("admin", "password", is_admin=True)
    dev = await WorkspaceStore(raw_session_factory, 1).create_shared("dev", admin.id)
    agent = await AgentStore(raw_session_factory, 1).create(dev, "bot", harness_url="https://h")
    other = await TraceSourceStore(raw_session_factory, 2, None).create(
        "phx", TraceProvider.PHOENIX, "https://phoenix.other", None
    )
    now = datetime.now(UTC)
    with pytest.raises(UnknownTraceSource):
        await TraceImportStore(raw_session_factory, 1).create(
            agent.id, other.id, "proj", now - timedelta(days=1), now, admin.id, "pod:test"
        )
