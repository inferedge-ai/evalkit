import pytest
from cryptography.fernet import Fernet

from evalkit.api.app import create_app
from evalkit.contracts.enums import TraceProvider
from evalkit.core.runner import RunnerDeps
from evalkit.db.crypto import SecretBox
from evalkit.db.store import PgStore
from evalkit.db.trace_sources import EncryptionKeyMissing, TraceSourceStore
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
    store = TraceSourceStore(session_factory, old)
    await store.create("phx", TraceProvider.PHOENIX, "https://p", "pk")
    listed = await store.list_sources()
    assert [s.name for s in listed] == ["phx"] and "api_key" not in listed[0].model_dump()
    resolved = await store.resolve("phx")
    assert resolved.api_key is not None and resolved.api_key.get_secret_value() == "pk"
    assert "pk" not in repr(resolved)


async def test_creating_without_a_box_is_refused(session_factory):
    with pytest.raises(EncryptionKeyMissing, match="encryption_key is not configured"):
        await TraceSourceStore(session_factory, None).create(
            "phx", TraceProvider.PHOENIX, "u", "pk"
        )


async def test_server_refuses_to_start_with_sources_but_no_key(tmp_path, session_factory):
    await TraceSourceStore(session_factory, box()).create("phx", TraceProvider.PHOENIX, "u", "pk")
    cfg = make_cfg(tmp_path).model_copy(update={"encryption_key": None})
    store = PgStore(session_factory)
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
