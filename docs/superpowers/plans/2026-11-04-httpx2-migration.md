# httpx2 migration

**Goal:** evalkit uses `httpx2` instead of `httpx` everywhere, with its own small
retry transport replacing `httpx-retries`, and `pytest-httpx2` replacing `respx`
in tests.

## Why

- `httpx` has had no release since 0.28.1 (December 2024) and its issue tracker
  is closed. Pydantic maintains `httpx2` as the continuation; Starlette, `mcp`,
  `fastmcp` and Authlib already use it, so the server extra installs both today.
- `httpx-retries` only works with `httpx2` when `sys.modules["httpx"]` is
  aliased to `httpx2` before it is imported (httpx-retries issue #73;
  httpx2 PR #1196 dropped it from httpx2's third-party list for that reason).
  `httpx2` documents that libraries must never alias, and `evalkit.sdk` runs
  inside client processes, so evalkit cannot alias.
- `httpx2.HTTPTransport(retries=n)` retries only `ConnectError` and
  `ConnectTimeout`; it has no status retries and no backoff.
- `pytest-httpx2` (by the respx author) is respx with an `httpx2_mock` fixture
  that patches `httpcore2`; it is on httpx2's third-party list.

## Scope

- Every `import httpx` / `from httpx ...` in `src/`, `tests/`, `scripts/`
  becomes `httpx2`. No `alias_httpx()` call and no `sys.modules` tricks
  anywhere.
- Dependencies, through `uv add` / `uv remove` only: add `httpx2`, remove
  `httpx` and `httpx-retries` from the base dependencies; in the dev group
  replace `respx` with `pytest-httpx2`. `httpx` may remain in `uv.lock` as a
  transitive dependency of other packages (testcontainers, respx under
  pytest-httpx2); evalkit code must not import it.
- `src/evalkit/client/retry.py` (new): `Retry` and `RetryTransport` for
  `httpx2`, covering exactly the behaviour `client/api.py` uses today, so the
  call sites change only their import.
- Sentry: if evalkit enables `sentry_sdk`'s httpx integration explicitly,
  switch to its `httpx2` integration.
- `CHANGELOG.md` entry under `## Unreleased`: the dependency change, and that
  code embedding `evalkit.sdk` now pulls `httpx2`.

## The retry transport

Behaviour to keep (today's `retry_policy` and `retry_extensions` in
`client/api.py`): total attempts, allowed methods, status force list
(502, 503, 504), exceptions to retry on (`NetworkError`, `ConnectTimeout`,
`RemoteProtocolError`), exponential backoff with a cap, honouring
`Retry-After` on a retried status, and a per-request override through
`request.extensions["retry"]`.

```
class Retry:  # frozen dataclass
    total, allowed_methods, status_forcelist, retry_on_exceptions,
    backoff_factor, max_backoff_wait, jitter   # jitter(a, b) defaults to random.uniform

    is_retryable_method(method) = method in allowed_methods
    backoff(attempt) = min(max_backoff_wait, backoff_factor * 2 ** (attempt + 1) * jitter(0, 1))   # attempt counts from 0
    wait(response | None, attempt) =
        retry_after(response) if response has a valid Retry-After (seconds or HTTP date), capped at max_backoff_wait
        else backoff(attempt)
    # full jitter, as in httpx-retries; at evalkit's defaults the first retry waits at most 1 s
    # a malformed Retry-After (a header evalkit does not control) falls back to backoff(attempt),
    # so parsing it carries a narrow try/except that the plan sanctions

class RetryTransport(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    __init__(transport, retry)   # raises TypeError unless transport is a sync or async httpx2 transport
    handle_request / handle_async_request(request):
        policy = request.extensions.get("retry", self.retry)
        for attempt in 0 .. policy.total:
            last = attempt == policy.total or not policy.is_retryable_method(request.method)
            send through the wrapped transport
              raised exception in retry_on_exceptions and not last -> sleep(wait(None, attempt)); continue
              raised anything else -> propagate
            response.status in status_forcelist and not last -> close response; sleep(wait(response, attempt)); continue
            return response
    close / aclose delegate to the wrapped transport
```

Sync sleeps with `time.sleep`, async with `anyio.sleep` (httpx2 depends on
anyio). A request body that is a non-replayable stream is sent once (no retry),
matching httpx-retries.

## Tests

- `tests/client/test_retry.py` (new): through `httpx2.MockTransport`, sync and
  async: retries a 503 then returns 200; gives up after `total` and returns the
  last 503; does not retry a POST under the read policy; retries a POST when the
  request carries the write policy in `extensions["retry"]`; `Retry(total=0)`
  never retries; retries `ConnectError` and re-raises after `total`; does not
  retry an exception outside `retry_on_exceptions`; `Retry-After: 2` waits 2 s
  (patch the sleep function, assert the waits); backoff grows and caps at
  `max_backoff_wait`.
- Existing retry tests in the suite keep passing unchanged except for imports.
- The seven files using respx move to the `httpx2_mock` fixture / respx router
  with `using="httpcore2"`.

## Done when

- `grep -rnE '^\s*(import httpx\b|from httpx\b|import respx|from httpx_retries)' src tests scripts` is empty.
- `uv run pytest -n auto`, `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy src/evalkit`, `uv run --group docs mkdocs build --strict` pass.
- `uv run python scripts/gen_schemas.py` leaves `contracts/` unchanged.
- A smoke run: `uv run evalkit serve` against a throwaway database and one CLI
  call (`evalkit whoami` after a password login) succeed.
