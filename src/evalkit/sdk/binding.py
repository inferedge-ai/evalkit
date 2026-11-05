from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ALLOWED = ("input", "files", "model", "metadata")


class Fixture(BaseModel):
    """A case's fixture file, downloaded before the task is called.

    Attributes:
        name: The fixture key the case names.
        path: Where the file sits on this machine.
        context: The file's description from the fixture registry, or `None`.
    """

    name: str
    path: Path
    context: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    params: tuple[str, ...]
    shortcut: bool


def inspect_task(task: Callable[..., Any]) -> TaskSpec:
    parameters = inspect.signature(task).parameters
    bindable = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    names = [p.name for p in parameters.values() if p.kind in bindable]
    if len(parameters) == 1 and names:
        return TaskSpec(params=(names[0],), shortcut=names[0] not in ALLOWED)
    required = [p.name for p in parameters.values() if p.default is p.empty and p.kind in bindable]
    unknown = [name for name in required if name not in ALLOWED]
    if unknown:
        raise TypeError(
            f"task parameter(s) not supported: {', '.join(unknown)}; allowed: {', '.join(ALLOWED)}"
        )
    return TaskSpec(params=tuple(name for name in names if name in ALLOWED), shortcut=False)


def arguments(
    spec: TaskSpec, *, input: dict, files: list[Fixture], model: str | None, metadata: dict
) -> tuple[list[Any], dict[str, Any]]:
    if spec.shortcut:
        return [input], {}
    values = {"input": input, "files": files, "model": model, "metadata": metadata}
    return [], {name: values[name] for name in spec.params}


async def call_task(task: Callable[..., Any], args: list[Any], kwargs: dict[str, Any]) -> Any:
    if inspect.iscoroutinefunction(task):
        return await task(*args, **kwargs)
    result = await asyncio.to_thread(task, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def ignores_model(spec: TaskSpec) -> bool:
    return spec.shortcut or "model" not in spec.params
