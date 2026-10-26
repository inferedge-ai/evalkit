# Login

```bash
evalkit login --url {{origin}}
```

The command asks for your username and password and stores the server URL and your token in `~/.evalkit/config.toml`. The CLI talks to one server at a time: logging in again, to the same server or another, replaces the stored login. Dev and prod are workspaces on one server (`finance@dev`, `finance@prod`), not separate logins.

Tokens expire after the TTL the deployer configured (30 days by default). `evalkit whoami` shows who the token logs in as and when it expires, and exits 1 if the server rejects it; run it before a long run. Commands that talk to the server warn when the token expires within a day. When commands start failing with 401, log in again.

## Scripts and CI

Two environment variables bypass the config file:

```bash
export EVALKIT_SERVER_URL={{origin}}
export EVALKIT_TOKEN=<token>
```

A stored token expires, so CI jobs should log in at the start of every run with a password from the secret store:

```bash
evalkit login --url "$EVALKIT_SERVER_URL" --username admin --password "$EVALKIT_ADMIN_PASSWORD"
```

## Timeout

The CLI and the SDK wait up to 120 seconds for each server call. Set `EVALKIT_TIMEOUT` (seconds) to wait longer, for example on a slow network:

```bash
export EVALKIT_TIMEOUT=300
```

It covers calls to the evalkit server only. The server's own calls to an agent's harness keep their fixed limits, and so does `evalkit harness check`.

The SDK uses the same login and the same two variables. A deployed agent that only exports traces uses an agent token instead; see [Tracing](tracing.md).
