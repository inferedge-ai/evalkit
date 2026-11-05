# End-to-end SSO flow

Against a built image (see the deployment skill's try-it stack) with this in
the server `config.yaml`:

```yaml
auth:
  oidc:
    issuer: http://127.0.0.1:19100
    client_id: evalkit-ui
    audience: http://localhost:8020
    name: Fake
  password_login: admin
```

1. Run `uv run python -m tests.e2e.services` in one terminal.
2. Run `evalkit login --url http://localhost:8020`. The fake authorize
   endpoint redirects straight back, so there is no browser step. Expect
   `logged in as e2e on http://localhost:8020`. `evalkit whoami` prints
   `login: sso (...)`.
3. Act as admin in a second shell, with its own config:
   - Get an admin token with a password login. `evalkit login --url
     http://localhost:8020 --username admin` writes the token to the config.
     Or call `curl -X POST http://localhost:8020/api/auth/login`.
   - Set `EVALKIT_SERVER_URL=http://localhost:8020` and `EVALKIT_TOKEN=<token>`.
   - Run `evalkit workspaces create dev`.
   - Run `evalkit workspaces members add dev e2e --role writer`.
   - Run `evalkit users add ci-bot --subject e2e-ci`.
   - Run `evalkit workspaces members add dev ci-bot --role writer`.
4. As e2e, run `evalkit sync skills/evalkit-deployment/try/cases/<suite>
   --workspace dev`. It succeeds.
5. As the machine client, set `EVALKIT_SERVER_URL=http://localhost:8020`,
   `EVALKIT_OIDC_CLIENT_ID=ci` and `EVALKIT_OIDC_CLIENT_SECRET=e2e-secret`.
   - `evalkit whoami` prints `ci-bot` and `login: client credentials`.
   - `evalkit run <suite>@dev --agent <agent>@dev` runs in the same
     environment.
6. Run `evalkit users archive e2e`. The next e2e command fails with 401
   `user is archived`.

## Web UI pass

The fake identity provider answers CORS preflights, so the web UI can call it
from the browser. `tests/e2e/services.py` reads three optional variables:
`E2E_IDP_AUDIENCE` (default `http://localhost:8020`), `E2E_IDP_ACCESS_TTL` in
seconds (default 3600; set it low to watch the UI refresh) and
`E2E_IDP_LOGIN` as `sub,email,preferred_username` (default
`e2e-sub,e2e@example.com,e2e`).
