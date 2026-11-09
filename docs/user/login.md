# Login

```bash
evalkit login --url {{origin}}
```

With single sign-on configured on the server, the command opens your browser
at your identity provider. Sign in there; the browser comes back to the CLI
on a local port, and the CLI stores the identity provider's tokens in
`~/.evalkit/config.toml`. They refresh themselves. On a machine without a
browser, `evalkit login --url {{origin}} --no-browser` prints a URL and a code
to enter on any device.

Without single sign-on, or with `--username`, the command asks for a username
and password and stores an evalkit token instead. With single sign-on
configured, password login is for admins unless the deployer set
`password_login: all`, and off for everyone with `password_login: none`.

The CLI talks to one server at a time: logging in again, to the same server
or another, replaces the stored login. Dev and prod are workspaces on one
server (`finance@dev`, `finance@prod`), not separate logins.

`evalkit whoami` shows who the login is, its kind (`sso`, `password` or
`client credentials`) and when the current token expires, and exits 1 if the
server rejects it. An SSO access token expires within the hour and renews
itself; that line is not a reason to log in again. When a command fails with
`your login has expired`, run `evalkit login` again.

The first sign-in creates your evalkit user, named after the part before `@`
in your identity provider's username or email. If that name is taken, the
command says so and names the subject an admin binds with
`evalkit users bind`; see [Single sign-on](sso.md).

## Scripts and CI

Pipelines authenticate as a machine client at the identity provider:

```bash
export EVALKIT_SERVER_URL={{origin}}
export EVALKIT_OIDC_CLIENT_ID=<client id>
export EVALKIT_OIDC_CLIENT_SECRET=<client secret>
```

No `evalkit login` is needed; every command fetches a token itself. An admin
binds the client once: see [CI sync](ci-sync.md). Without single sign-on,
`EVALKIT_SERVER_URL` and `EVALKIT_TOKEN` carry an evalkit token, or the job
logs in with a dedicated non-admin password user.

## Timeout

The CLI and the SDK wait up to 120 seconds for each server call. Set `EVALKIT_TIMEOUT` (seconds) to wait longer, for example on a slow network:

```bash
export EVALKIT_TIMEOUT=300
```

It covers calls to the evalkit server only. The server's own calls to an agent's harness keep their fixed limits, and so does `evalkit harness check`.

The SDK uses the same login and the same variables. A deployed agent that
only exports traces uses an agent token; see [Tracing](tracing.md).
