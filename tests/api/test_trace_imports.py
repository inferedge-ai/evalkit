import asyncio

from evalkit.api.trace_imports import interrupt_imports
from evalkit.contracts.enums import TraceImportStatus


class Finished:
    def __init__(self):
        self.calls: list[tuple[int, str, TraceImportStatus, str | None]] = []

    async def finish(self, import_id, holder, status, error=None):
        self.calls.append((import_id, holder, status, error))


async def test_shutdown_cancels_running_imports_and_marks_them_failed():
    store = Finished()
    running = asyncio.create_task(asyncio.Event().wait())
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    await interrupt_imports(store, {4: ("pod:a", running), 5: ("pod:b", done)})  # type: ignore[arg-type]
    assert running.cancelled()
    assert store.calls == [
        (4, "pod:a", TraceImportStatus.FAILED, "the server stopped while importing")
    ]
