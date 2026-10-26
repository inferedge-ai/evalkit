import pytest

from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.remote_evaluators import UnknownEvaluator
from evalkit.db.evaluators import EvaluatorExists, InvalidEvaluator, RemoteEvaluatorStore
from tests.identity import seed_identity

SETTINGS = RemoteEvaluatorSettings(endpoint="http://e")


@pytest.fixture
async def store(session_factory):
    await seed_identity(
        session_factory, {"admin": "password", "akshat": "password"}, shared=("dev",)
    )
    return RemoteEvaluatorStore(session_factory)


@pytest.mark.parametrize("name", ["", "a@b"])
async def test_names_without_at_sign(store, name):
    with pytest.raises(InvalidEvaluator):
        await store.create("dev", name, EvaluatorKind.SCORE, SETTINGS)


async def test_per_workspace_names_and_wire_kind(store):
    await store.create("dev", "compliance", EvaluatorKind.SCORE, SETTINGS)
    await store.create("akshat", "compliance", EvaluatorKind.GAUGE, SETTINGS)
    dev = await store.in_workspace("dev")
    assert dev["compliance"].wire_kind == "evaluator" and dev["compliance"].ref == "compliance@dev"
    assert (await store.in_workspace("akshat"))["compliance"].wire_kind == "gauge"
    assert await store.names() == {"compliance"}
    with pytest.raises(EvaluatorExists):
        await store.create("dev", "compliance", EvaluatorKind.SCORE, SETTINGS)


async def test_edit_and_archive(store):
    await store.create("dev", "compliance", EvaluatorKind.SCORE, SETTINGS)
    edited = await store.edit("compliance@dev", RemoteEvaluatorSettings(endpoint="http://f"))
    assert edited.settings.endpoint == "http://f"
    await store.archive("compliance@dev")
    assert await store.in_workspace("dev") == {}
    with pytest.raises(UnknownEvaluator):
        await store.get("nope@dev")
