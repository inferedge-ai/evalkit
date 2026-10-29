import pytest
from cryptography.fernet import Fernet

from evalkit.contracts.enums import EvaluatorKind, TraceProvider
from evalkit.contracts.settings import JudgeSettings, RemoteEvaluatorSettings
from evalkit.db.agents import AgentStore
from evalkit.db.crypto import SecretBox
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.llm_providers import (
    LlmProviderExists,
    LlmProviderStore,
    ProviderInUse,
    UnknownLlmProvider,
)
from evalkit.db.rekey import count_sealed, rekey_all
from evalkit.db.trace_sources import TraceSourceStore
from tests.identity import seed_agent, seed_identity


def box() -> SecretBox:
    return SecretBox(Fernet.generate_key().decode())


async def test_agent_key_is_cleared_when_the_url_changes(session_factory):
    await seed_identity(session_factory, {"admin": "password1"})
    await seed_agent(session_factory)
    agents = AgentStore(session_factory)
    sealed = box().seal("k")
    assert (await agents.edit("fake@canonical", api_key_encrypted=sealed)).has_api_key
    assert await agents.sealed_key("fake@canonical") == sealed
    same = await agents.edit(
        "fake@canonical", harness_url=(await agents.get("fake@canonical")).harness_url
    )
    assert same.has_api_key
    moved = await agents.edit("fake@canonical", harness_url="https://elsewhere/v1")
    assert not moved.has_api_key
    rekeyed = await agents.edit(
        "fake@canonical", harness_url="https://third/v1", api_key_encrypted=sealed
    )
    assert rekeyed.has_api_key
    cleared = await agents.edit("fake@canonical", clear=frozenset({"api_key"}))
    assert not cleared.has_api_key


async def test_evaluator_key_follows_its_endpoint(session_factory):
    await seed_identity(session_factory, {"admin": "password1"})
    store = RemoteEvaluatorStore(session_factory)
    settings = RemoteEvaluatorSettings(endpoint="https://e/rtc")
    sealed = box().seal("k")
    created = await store.create("canonical", "rtc", EvaluatorKind.SCORE, settings, sealed)
    assert created.has_api_key
    assert await store.sealed_keys("canonical") == {"rtc": sealed}
    kept = await store.edit("rtc@canonical", settings.model_copy(update={"timeout_seconds": 5}))
    assert kept.has_api_key
    moved = await store.edit("rtc@canonical", RemoteEvaluatorSettings(endpoint="https://f/rtc"))
    assert not moved.has_api_key
    await store.archive("rtc@canonical")
    assert not (await store.restore("rtc@canonical")).archived


async def test_llm_providers_and_the_default_judge(session_factory):
    store = LlmProviderStore(session_factory)
    assert await store.default_judge() is None
    sealed = box().seal("k")
    created = await store.create("openai", "https://api.openai.com/v1", sealed)
    assert created.has_api_key and created.model_dump().keys() == {
        "name",
        "base_url",
        "has_api_key",
        "archived",
    }
    with pytest.raises(LlmProviderExists):
        await store.create("other", "https://api.openai.com/v1", None)
    with pytest.raises(UnknownLlmProvider):
        await store.set_default_judge(JudgeSettings(base_url="https://nope/v1", model="m"))
    judge = JudgeSettings(base_url="https://api.openai.com/v1", model="gpt-5")
    await store.set_default_judge(judge)
    assert await store.default_judge() == judge
    assert await store.sealed_key("https://api.openai.com/v1") == sealed
    with pytest.raises(ProviderInUse):
        await store.archive("openai")
    with pytest.raises(ProviderInUse):
        await store.edit("openai", base_url="https://moved/v1")
    assert not (await store.edit("openai", clear_api_key=True)).has_api_key

    await store.create("local", "http://localhost:11434/v1", None)
    await store.archive("local")
    assert await store.active_by_base_url("http://localhost:11434/v1") is None
    assert [p.name for p in await store.list_providers()] == ["openai"]


async def test_rekey_covers_every_sealed_table(session_factory):
    await seed_identity(session_factory, {"admin": "password1"})
    await seed_agent(session_factory)
    old, new = box(), box()
    await TraceSourceStore(session_factory, old).create(
        "phx", TraceProvider.PHOENIX, "https://p", "pk"
    )
    await AgentStore(session_factory).edit("fake@canonical", api_key_encrypted=old.seal("ak"))
    await LlmProviderStore(session_factory).create("openai", "https://o/v1", old.seal("lk"))
    assert await count_sealed(session_factory) == 3
    assert await rekey_all(session_factory, old, new) == 3
    assert new.open(await AgentStore(session_factory).sealed_key("fake@canonical") or b"") == "ak"
    assert (
        new.open(await LlmProviderStore(session_factory).sealed_key("https://o/v1") or b"") == "lk"
    )
