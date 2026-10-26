from pathlib import Path

import pytest

from evalkit.sdk.binding import Fixture, arguments, call_task, inspect_task

FILES = [Fixture(name="a.csv", path=Path("/tmp/a.csv"), context="ctx")]
META = {"experiment_id": "e", "case_id": "c", "repetition": 1, "tags": ["t"]}


def test_one_argument_shortcut_gets_input():
    spec = inspect_task(lambda q: q)
    assert spec.shortcut
    args, kwargs = arguments(spec, input={"question": "q"}, files=FILES, model=None, metadata=META)
    assert args == [{"question": "q"}] and kwargs == {}


def test_named_parameters_bind_by_name_in_any_order():
    def task(metadata, files, input, model="x"):
        return input

    spec = inspect_task(task)
    args, kwargs = arguments(spec, input={"q": 1}, files=FILES, model=None, metadata=META)
    assert args == []
    assert kwargs == {"metadata": META, "files": FILES, "input": {"q": 1}, "model": None}


def test_unknown_parameter_without_default_is_rejected():
    def task(input, expected):
        return input

    with pytest.raises(TypeError, match="expected"):
        inspect_task(task)

    def task_ok(input, extra=None):
        return input

    assert inspect_task(task_ok).params == ("input",)


async def test_call_task_sync_and_async():
    assert await call_task(lambda q: q + "!", ["a"], {}) == "a!"

    async def atask(input):
        return input * 2

    assert await call_task(atask, [], {"input": 2}) == 4
