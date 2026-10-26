from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.sessions import SessionTooLarge, SessionTrajectory


class Reader:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def trajectory(self, agent, session_id):
        self.calls += 1
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def clock_from(times):
    it = iter(times)
    return lambda: next(it)


async def no_sleep(_):
    return None


async def test_returns_valid_once_settled_with_root():
    reader = Reader(
        [
            SessionTrajectory(settled=False, root_present=True, span_count=1, trajectory={"a": 1}),
            SessionTrajectory(settled=True, root_present=True, span_count=2, trajectory={"a": 2}),
        ]
    )
    doc, status = await poll_trajectory(
        reader, "agent", "s", 60, clock=clock_from([0, 1, 2, 3]), sleep=no_sleep
    )
    assert (doc, status) == ({"a": 2}, "valid") and reader.calls == 2


async def test_deadline_partial_when_root_present():
    reader = Reader(
        [SessionTrajectory(settled=False, root_present=True, span_count=1, trajectory={"p": 1})]
    )
    doc, status = await poll_trajectory(
        reader, "agent", "s", 10, clock=clock_from([0, 11, 12]), sleep=no_sleep
    )
    assert (doc, status) == ({"p": 1}, "partial")


async def test_deadline_unavailable_without_root():
    reader = Reader([SessionTrajectory(settled=False, root_present=False, span_count=0)])
    doc, status = await poll_trajectory(
        reader, "agent", "s", 10, clock=clock_from([0, 11, 12]), sleep=no_sleep
    )
    assert (doc, status) == (None, "unavailable")


class TooLarge:
    def __init__(self):
        self.calls = 0

    async def trajectory(self, agent, session_id):
        self.calls += 1
        raise SessionTooLarge("session s holds 900.0 MiB of span attributes")


async def test_oversized_session_is_unavailable_without_retrying(caplog):
    reader = TooLarge()
    doc, status = await poll_trajectory(
        reader, "agent", "s", 60, clock=clock_from([0, 1, 2]), sleep=no_sleep
    )
    assert (doc, status) == (None, "unavailable") and reader.calls == 1
    assert "900.0 MiB" in caplog.text
