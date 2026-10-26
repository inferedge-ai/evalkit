from datetime import datetime, timedelta

from evalkit.core.traces.raw import RawSpan


def settled(spans: list[RawSpan], now: datetime, settle_seconds: int) -> bool:
    received = [s.received_at for s in spans if s.received_at is not None]
    if not received:
        return False
    return max(received) < now - timedelta(seconds=settle_seconds)


def root_present(spans: list[RawSpan]) -> bool:
    return any(s.parent_span_id is None for s in spans)
