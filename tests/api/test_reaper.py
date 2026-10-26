import asyncio
import signal

from evalkit.api.app import reap_forever, stop_if_reaper_died


async def test_a_dead_reaper_stops_the_server(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr("evalkit.api.app.os.kill", lambda pid, sig: killed.append(sig))

    async def broken() -> None:
        raise RuntimeError("database failed over")

    task = asyncio.create_task(broken())
    await asyncio.gather(task, return_exceptions=True)
    stop_if_reaper_died(task)
    assert killed == [signal.SIGTERM]


async def test_a_cancelled_reaper_is_a_normal_shutdown(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr("evalkit.api.app.os.kill", lambda pid, sig: killed.append(sig))
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    stop_if_reaper_died(task)
    assert killed == []


async def test_the_reaper_rides_out_a_short_blip(monkeypatch):
    calls: list[int] = []

    async def flaky(session_factory) -> list[int]:
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("connection reset")
        if len(calls) == 4:
            raise asyncio.CancelledError
        return []

    monkeypatch.setattr("evalkit.api.app.reap", flaky)
    task = asyncio.create_task(reap_forever(None, interval=0, give_up_after=60))
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and len(calls) == 4


async def test_the_reaper_gives_up_when_passes_keep_failing(monkeypatch):
    async def down(session_factory) -> list[int]:
        raise RuntimeError("database gone")

    monkeypatch.setattr("evalkit.api.app.reap", down)
    task = asyncio.create_task(reap_forever(None, interval=0.01, give_up_after=0.03))
    await asyncio.gather(task, return_exceptions=True)
    assert isinstance(task.exception(), RuntimeError)
