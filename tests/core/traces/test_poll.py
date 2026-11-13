from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.session_import import SessionPullFailed
from evalkit.core.traces.sessions import SessionTooLarge, SessionTrajectory


class Reader:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def trajectory(self, agent, session_id):
        self.calls += 1
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def clock_from(times):
    it = iter(times)
    return lambda: next(it)


async def no_sleep(_):
    return None


async def poll(responses, deadline=10, times=(0, 11, 12), **kwargs):
    reader = Reader(responses)
    result = await poll_trajectory(
        reader, "agent", "s", deadline, clock=clock_from(times), sleep=no_sleep, **kwargs
    )
    return result, reader


def session(**fields) -> SessionTrajectory:
    return SessionTrajectory(**{"settled": False, "root_present": False, "span_count": 1, **fields})


async def test_returns_valid_once_settled_with_root():
    result, reader = await poll(
        [
            session(root_present=True, trajectory={"a": 1}),
            session(settled=True, root_present=True, span_count=2, trajectory={"a": 2}),
        ],
        deadline=60,
        times=[0, 1, 2, 3],
    )
    assert result == ({"a": 2}, "valid", None) and reader.calls == 2


async def test_valid_carries_the_builders_reason():
    result, _ = await poll(
        [session(settled=True, root_present=True, trajectory={"a": 1}, reason="no steps")]
    )
    assert result == ({"a": 1}, "valid", "no steps")


async def test_deadline_partial_says_spans_are_still_arriving():
    result, _ = await poll(
        [session(root_present=True, trajectory={"p": 1})], wait_setting="import_wait_seconds"
    )
    assert result == (
        {"p": 1},
        "partial",
        "spans still arriving after 10 s; raise import_wait_seconds",
    )


async def test_deadline_unavailable_with_spans_but_no_root():
    result, _ = await poll([session()])
    assert result == (
        None,
        "unavailable",
        "spans still arriving after 10 s; raise trajectory_wait_seconds",
    )


async def test_deadline_unavailable_with_nothing_found():
    result, _ = await poll([session(span_count=0)], origin="in project ra of trace source phx")
    doc, status, reason = result
    assert (doc, status) == (None, "unavailable")
    assert reason.startswith("session s not found in project ra of trace source phx after 10 s.")


async def test_a_settled_run_span_alone_is_unavailable_with_its_reason():
    result, _ = await poll(
        [session(settled=True, root_present=True, only_run_span=True, reason="only run span")],
        deadline=60,
        times=[0, 1],
    )
    assert result == (None, "unavailable", "only run span")


async def test_a_failed_pull_keeps_polling():
    result, reader = await poll(
        [
            SessionPullFailed("phx: boom"),
            session(settled=True, root_present=True, trajectory={"a": 1}),
        ],
        deadline=60,
        times=[0, 1, 2],
    )
    assert result == ({"a": 1}, "valid", None) and reader.calls == 2


async def test_every_pull_failing_ends_unavailable_with_the_last_error():
    result, _ = await poll([SessionPullFailed("phx: boom")])
    assert result == (None, "unavailable", "phx: boom")


class TooLarge:
    def __init__(self):
        self.calls = 0

    async def trajectory(self, agent, session_id):
        self.calls += 1
        raise SessionTooLarge("session s holds 900.0 MiB of span attributes")


async def test_oversized_session_is_unavailable_without_retrying(caplog):
    reader = TooLarge()
    doc, status, reason = await poll_trajectory(
        reader, "agent", "s", 60, clock=clock_from([0, 1, 2]), sleep=no_sleep
    )
    assert (doc, status) == (None, "unavailable") and reader.calls == 1
    assert "900.0 MiB" in caplog.text and "900.0 MiB" in reason
