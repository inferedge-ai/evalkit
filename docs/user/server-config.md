# Server config: `config.yaml`

`config.yaml` configures one evalkit server. `evalkit serve` and `evalkit db upgrade` read it (`--config`, default `./config.yaml`) and load the `.env` beside it; the environment wins. It is not `evalkit.yaml`, the developer config ([Config](config.md)).

Any value can be `env:NAME` (required) or `env:NAME:<default>`. Unknown keys fail the start.

`evalkit init-server` writes one for a single machine, with a smaller database pool than the defaults below.

## Database pool and scoring

| Key | Default | `init-server` | What it does |
| -- | -- | -- | -- |
| `db.pool_size` | `20` | `5` | Connections one server keeps open to Postgres. |
| `db.max_overflow` | `10` | `5` | Extra connections opened under load. |
| `db.pool_timeout_seconds` | `5` | `5` | A request that waits this long for a connection gets `503` with `Retry-After`. |
| `runner.evaluation_concurrency` | `16` | `4` | Runs a server run scores at once; also the most a run may ask for. |

- One server holds at most `pool_size + max_overflow` connections.
- Keep `(pool_size + max_overflow) × servers` well under Postgres `max_connections` (`100` on a stock Postgres), with room for migrations and `psql`. Ten servers at the defaults need 300 and fail with `FATAL: sorry, too many clients already`.
- Keep `evaluation_concurrency` under `pool_size`: each run being scored holds a connection while it saves.
- The CLI and the SDK retry a `503`. A server run does not; if one fails for lack of a connection, `evalkit evaluate <experiment>` finishes it.

## Every key

| Key | Default | What it does |
| -- | -- | -- |
| `server.base_url` | required | The URL in links the CLI prints. `evalkit serve` binds its port (`8020` when it has none) unless `--port` is given. |
| `server.accent_color` | none | A CSS color for the web UI's accent. |
| `db.url` | required | Postgres URL, e.g. `postgresql+psycopg://localhost/evalkit`. |
| `db.migrate_on_start` | `false` | Run `evalkit db upgrade` at startup. |
| `db.pool_size`, `db.max_overflow`, `db.pool_timeout_seconds` | `20`, `10`, `5` | See [Database pool and scoring](#database-pool-and-scoring). |
| `blob.url` | required | Where trajectories, artifacts and fixtures go: `file:///path` or `s3://bucket/prefix`. |
| `runner.evaluation_concurrency` | `16` | See [Database pool and scoring](#database-pool-and-scoring). |
| `cost_map.source` | `bundled` | The LiteLLM cost map: `bundled`, or a path or URL. |
| `auth.token_ttl_hours` | `720` | How long a login token lasts. |
| `mcp.oauth` | none | Also accept an OAuth provider's JWTs at `/mcp`: `issuer`, `jwks_uri`, `audience` (none), `algorithm` (`RS256`), `scope` (`evalkit:read`). See [MCP](mcp.md). |
| `otel.settle_seconds` | `10` | A session is settled when its newest span arrived longer ago than this. |
| `otel.max_batch_bytes` | `16777216` (16 MB) | One OTLP export request; `413` beyond it. |
| `otel.max_session_bytes` | `402653184` (384 MB) | Span attributes one session read loads; `413` beyond it. Size it with the server's memory. |
| `otel.import_page_traces` | `25` | Traces per request to a trace source during an import. |
| `limits.fixture_upload_bytes` | `524288000` (500 MB) | One fixture file uploaded by `evalkit sync`. |
| `limits.experiment_blob_bytes` | `524288000` (500 MB) | One trajectory or artifact written by a local run. |
| `limits.case_upload_bytes` | `52428800` (50 MB) | The case YAML of one `evalkit sync` or lint. |
| `limits.request_bytes` | `10485760` (10 MB) | Any other request body. |
| `sentry` | none | Report server errors to Sentry: `dsn` (required), `environment`, `traces_sample_rate` (`0.0`), `max_request_body_size` (`never`, `small`, `medium` or `always`). |
| `encryption_key` | none | Fernet key that encrypts stored API keys. The server refuses to start without it once any exist. |
| `allow_private_endpoints` | `false` | Let agents, evaluators, LLM providers and trace sources point at loopback, private or link-local addresses. |

LLM providers, the default judge and stored API keys live in the database, not here: set them with `evalkit llm-providers`, `evalkit judge-default set` and `evalkit agents edit`.
