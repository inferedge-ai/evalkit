from datetime import datetime, timedelta

from evalkit.core.traces.raw import RawSpan


def settled(spans: list[RawSpan], now: datetime, settle_seconds: int) -> bool:
    received = [s.received_at for s in spans if s.received_at is not None]
    return is_settled_since(max(received, default=None), now, settle_seconds)


def is_settled_since(last_received: datetime | None, now: datetime, settle_seconds: int) -> bool:
    if last_received is None:
        return False
    return last_received < now - timedelta(seconds=settle_seconds)


def root_present(spans: list[RawSpan]) -> bool:
    return any(s.parent_span_id is None for s in spans)
