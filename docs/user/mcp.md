# MCP

The server is also an MCP server at `{{origin}}/mcp` (streamable HTTP). An agent connected to it can read every agent's runs and trajectories, and every experiment's scores. It cannot change anything.

## Connect

With an evalkit login token (`evalkit login` stores it in `~/.evalkit/config.toml`):

```bash
claude mcp add --transport http evalkit {{origin}}/mcp --header "Authorization: Bearer $EVALKIT_TOKEN"
```

Only login tokens work here: agent tokens (`evalkit tokens create`) and download tickets are refused.

When the server's config has an `mcp.oauth` block, MCP clients that speak OAuth (Claude, Cursor, Codex) can connect with the URL alone: the server points them to the identity provider, and a token issued for `{{origin}}/mcp` with the configured scope (`mcp.oauth.scope`, default `evalkit:read`) is accepted. Evalkit login tokens keep working next to it.

```yaml
mcp:
  oauth:
    issuer: https://id.example.com
    jwks_uri: https://id.example.com/.well-known/jwks.json
    # audience: defaults to <server.base_url>/mcp
    # scope: your identity provider's name for read access; defaults to evalkit:read
```

An OAuth token is not an evalkit user. Whoever the identity provider gives that scope reads everything an evalkit login reads, which today is every workspace, and archiving someone in evalkit does not cut off their OAuth access: revoke it at the identity provider.

## Tools

| Tool | What it returns |
|---|---|
| `find_agents` | Agents and the id (`name@workspace`) the run tools take |
| `find_runs` | An agent's runs in a time window, newest first: id, title, time, `user_id`, `org_id`, app or eval. `include` adds each run's `metrics`, `turns` or `tool_calls` (what it called, with which arguments). `failed_only` keeps runs with a failure; `tool_error` keeps runs where that tool errored. `timezone` reads date-only bounds as `get_run_stats` does. Up to 5,000 rows when listing alone, 200 with `include` or a filter |
| `get_runs` | Up to 200 runs by id: turns, failed turns, failures, duration, cost and where it came from, tokens, cost by model, tool errors by tool, opening question, final answer; `include` adds `turns` and `tool_calls` |
| `get_run_stats` | Totals over a time window, or one row per day, user, source, or tool that errored; `group_by` takes several at once and reads the window once |
| `get_trajectory` | One run's ATIF trajectory, paged by step, long text clipped |
| `get_run_files`, `download_run_file` | Files the run produced |
| `find_experiments`, `get_experiments`, `get_experiment_runs` | Experiments, their scorecard, and per-case scores with evaluator explanations |

Every tool answers `{data, meta}`, with `data` a list of rows. Search tools page with `limit` and `offset` (`meta.next_offset` is the next page); tools that take ids answer one row per id in input order, with `note: {"code": "not_found"}` for an unknown one. Dates are `start_date` / `end_date` (`YYYY-MM-DD`, inclusive, or an ISO 8601 timestamp). Money is `{"value", "unit": "currency", "currency": "USD"}`. Errors read `<code>: <detail>`. Tools that read trajectories have a time budget for the whole call, listing included: a run not read in time comes back as a row with `error` and `detail` instead of metrics, counted in `meta.failed` by `get_runs`; ask for it again with `get_runs`. Optional fields with no value are left out of rows. When listing alone uses the budget, the call fails with `harness_timeout`; narrow the window. With `failed_only` or `tool_error`, `find_runs` reads the window again on every call, so take one page and fetch the runs not read with `get_runs` rather than paging on.

A turn is one user message and everything the agent did until the next one. `cost_source` is `reported` when the harness priced the run, `estimated` when evalkit priced it from token counts, `mixed` for both. A turn counts as failed when the harness marks a step in it with `extra.is_error` (see [Harness API](harness-api.md)).

`user_id` and `org_id` are what the harness recorded. Resolve them to people and firms with your identity service.
