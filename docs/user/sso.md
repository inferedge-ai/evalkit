# Single sign-on

Evalkit signs people in through your identity provider (IdP) with OpenID
Connect. The web UI and the CLI are public OAuth clients: each sends you to
the IdP, receives an access token, and sends that token to evalkit with every
request. Evalkit verifies it with the IdP's published keys and never issues a
login token of its own for SSO users. CI pipelines use a machine client at the
IdP. This page is for the IdP admin who registers evalkit and the evalkit admin
who turns SSO on.

## What evalkit is to the IdP

| Piece | At the IdP | Standard |
| --- | --- | --- |
| The web UI and the CLI | One public client (no secret) | Authorization code with PKCE, RFC 6749 §4.1 and RFC 7636; refresh, RFC 6749 §6 |
| The CLI on a machine without a browser | The same client | Device authorization grant, RFC 8628 |
| Evalkit's API | An API or resource that access tokens name in `aud` | JWT access tokens, RFC 9068 |
| A CI pipeline | One machine client per pipeline, with a secret | Client credentials, RFC 6749 §4.4 |
| Discovery | `<issuer>/.well-known/openid-configuration` | OpenID Connect Discovery 1.0 |

## Requirements

Check each before registering. Evalkit does not work around any of them.

| Requirement | How to check |
| --- | --- |
| OIDC discovery at the issuer | `curl <issuer>/.well-known/openid-configuration` returns `jwks_uri`, `authorization_endpoint`, `token_endpoint` |
| JWT access tokens, not opaque strings, that carry evalkit's audience in `aud` | Decode an access token issued for evalkit (`jwt.io` or `evalkit whoami` after a sign-in); it has three dot-separated parts and `aud` contains the value you configure as `auth.oidc.audience`. Google and Okta's default authorization server issue opaque tokens and cannot be used; Okta needs a custom authorization server |
| `sub`, `exp`, `iat` in every access token | Same decoded token |
| `email` and, when the IdP has one, `preferred_username` in the ID token | Grant the `email` and `profile` scopes to the client; evalkit refuses a first sign-in whose ID token has no `email` |
| PKCE with S256 for the public client | A setting on the client registration |
| Refresh tokens for the public client, with rotation on | Required, not recommended: the web UI keeps its refresh token in browser storage and rotation is what bounds the damage of a stolen one |
| The client credentials grant for machine clients | A setting on each machine client |
| The device authorization grant, optional | Needed only for `evalkit login --no-browser` |
| CORS on the discovery document and the token endpoint for evalkit's origin | The web UI calls both from the browser. `curl -i -H 'Origin: https://<evalkit>' <token_endpoint>` answers with `Access-Control-Allow-Origin` |

## Registering the public client

One registration, shared by the UI and the CLI. Application type: public,
single-page or native, no client secret. Redirect URIs:

- `https://<evalkit>/login/callback` for the web UI.
- `http://127.0.0.1/callback` for the CLI, with any port (RFC 8252 §7.3; Okta
  and Entra accept the loopback with a wildcard port, Keycloak accepts
  `http://127.0.0.1:*/callback`).

Scopes to allow: `openid`, `profile`, `email`, `offline_access` (or the IdP's
name for refresh tokens). PKCE S256 on. Refresh token rotation on. Copy the
client id into `auth.oidc.client_id`.

## Registering the audience

Access tokens must name evalkit. How that is set up depends on the IdP:

| IdP | How | What goes in config |
| --- | --- | --- |
| Entra ID | Expose an API on the app registration with a scope such as `api://<app id>/evalkit`; grant the public client that scope | `audience: api://<app id>`; add the scope to `scopes` |
| Okta (custom authorization server) | Set the server's audience to evalkit's URL | `audience: https://evalkit.example.com`; `authorize_params: {resource: https://evalkit.example.com}` if the server needs a resource indicator |
| Auth0 | Create an API with evalkit's URL as identifier | `audience: https://evalkit.example.com`; `authorize_params: {audience: https://evalkit.example.com}` |
| Keycloak | Add an audience mapper for the client | `audience: <the mapped value>` |
| Any IdP that follows RFC 8707 | Resource indicators | `authorize_params: {resource: <audience>}` |

Evalkit sends `authorize_params` verbatim on the authorize request and the
device authorization request; the server never interprets them.

## Registering a machine client for CI

One per pipeline. Application type: machine-to-machine or service, client
credentials grant on, with the same audience attached. Note the client id
and secret: they go into the pipeline's secrets as `EVALKIT_OIDC_CLIENT_ID`
and `EVALKIT_OIDC_CLIENT_SECRET`. Note the subject the IdP puts in `sub` for
this client's tokens (Okta and Auth0 derive it from the client id; Entra uses
the service principal's object id; Keycloak the id of the client's service
account user): an evalkit admin binds it with `evalkit users add ci-bot
--subject <sub>`. See [CI sync](ci-sync.md).

## The evalkit side

In the server's `config.yaml`:

    auth:
      token_ttl_hours: 720          # password login tokens
      password_login: admin         # admin (default), all or none; ignored without oidc
      oidc:
        issuer: https://id.example.com
        client_id: 0oa1b2c3d4e5f6g7h8
        audience: https://evalkit.example.com
        name: Okta                  # the login button's label
        scopes: [openid, profile, email, offline_access]
        required_scopes: []         # scopes an access token must carry
        authorize_params: {}
        algorithms: [RS256]
        admin_claim: null           # {name: roles, value: evalkit-admin}: who is an admin

`password_login: admin` keeps password login for admins only, so the
bootstrap `admin` account stays a break-glass login while everyone else uses
SSO. `all` runs both side by side. Restart the server and check
`GET /api/auth/providers`: it lists the IdP's name and endpoints, or answers
503 while the IdP is unreachable (the server still starts, and admin password
login keeps working).

`none` turns password login off: everyone signs in through the identity
provider. It needs `admin_claim` (see below), because the IdP then says who is
an admin, and the server no longer makes the password-only `admin` account or
needs `EVALKIT_ADMIN_PASSWORD`. A signed-out visitor goes straight to the
provider instead of seeing evalkit's login page, and `POST /api/auth/login`
refuses every password.

### Requiring a scope

Some IdPs issue evalkit-audience tokens to anyone with an account. Set
`auth.oidc.required_scopes` to a scope that only authorised people get, and
evalkit refuses every other IdP token:

    auth:
      oidc:
        scopes: [openid, profile, email, offline_access, evalkit:read]
        required_scopes: [evalkit:read]

The server refuses to start when a required scope is not also in `scopes`:
the UI, the CLI and MCP hosts only request the scopes listed there.

- The check covers `POST /api/auth/oidc/register` (no user is created), every
  `/api` request with an IdP token, and `/mcp`.
- evalkit reads the scopes from the access token's `scope` claim (a
  space-separated string), its `scp` claim (a list, or a space-separated
  string), or both.
- A token that lacks one answers 403 `access token lacks required scope
  <scope>` on `/api` and on register. This check runs before evalkit looks for
  the user, so a token without the scope gets 403 even for an unregistered
  identity. `/mcp` answers 403 `insufficient_scope` ("Required scope:
  <scope>") with a `WWW-Authenticate` header.
- Put each required scope in `auth.oidc.scopes` too, so the UI and the CLI
  ask the IdP for it.
- The CLI's CI login (client credentials) asks the IdP for the required
  scopes, which `GET /api/auth/providers` lists as `required_scopes`. Let the
  CI machine client request them at the IdP. It does not ask for the rest of
  `scopes`, because IdPs such as Okta refuse `openid` and `offline_access`
  there.
- Password login tokens and agent tokens are not checked. An empty list (the
  default) turns the check off.

### Several organisations

Everyone who signs in lands in one tenant. A deployment that serves several
organisations from one server runs evalkit inside a wrapper that owns its
tenants: it passes `create_app(resolve_tenant=...)`, which reads the
organisation from the access token and returns its id, which evalkit stores
on every row as text (1-64 letters, digits, `-` or `_`; anything else answers
403). A server without a wrapper puts everything in `default`. evalkit keeps no
list of tenants itself.

The wrapper resolves every token the same way, CI's client credentials tokens
included, so the IdP must put the organisation in those too. When the wrapper
returns no tenant, the request answers 403 `no tenant for this request`;
`/mcp` can only accept or refuse a token, so there it answers 401.

### Admins from the IdP

    auth:
      oidc:
        admin_claim:
          name: roles               # the access token claim
          value: evalkit-admin      # the value that makes an admin

With `admin_claim` set, every registration (each sign-in) sets the person's
admin flag from the access token: admin when the claim equals the value, or
is a list that contains it; not an admin otherwise. A person the IdP stops
naming as an admin loses admin at their next sign-in, even when they are the
tenant's last admin: the IdP owns admins once this is on, so `evalkit users
demote`'s last-admin check does not apply. Unset (the default),
registration never changes the flag, and admins are promoted with
`evalkit users promote`. An admin's powers stay inside their tenant: users,
workspaces and members, LLM providers and the default judge, trace sources
and agent tokens of that tenant only.

`mcp.oauth` is gone: `/mcp` verifies tokens with the same settings as `/api`,
and an IdP token is an evalkit user there too.

## Signing in to the web UI

In the web UI, the login page shows **Continue with <provider>** when single
sign-on is configured. The first sign-in creates your evalkit user from the
identity provider's account; later sign-ins find it. An admin can still use
the username and password form behind **Admin sign-in** (or under the button
when `auth.password_login` is `all`). With `password_login: none` there is no
login page to see: evalkit sends you straight to the provider, and back to the
page you opened. The profile button at the bottom of the sidebar shows your
email under your username. Sign out clears the session in this browser only;
your identity provider session stays. With `none`, signing out shows a
**Sign in again** button rather than sending you back to the provider at once,
in every tab of that browser, until you next sign in.

## Admin runbook

1. Start with `EVALKIT_ADMIN_PASSWORD` set; `admin` is a password account.
   With `password_login: none` there is no password account and no
   `EVALKIT_ADMIN_PASSWORD`: the IdP names the admins through `admin_claim`.
2. Ask the first person to sign in; evalkit creates their user on the first
   sign-in. Then `evalkit users promote <username>`.
   `password_login: none` requires `admin_claim`, so whoever the claim names
   is an admin from their first sign-in and, as with `admin_claim` in any
   mode, promoting by hand does not last past the next one. If
   nobody can sign in as an admin (the claim is missing from the access
   token, say), switch back to `password_login: admin` and start with
   `EVALKIT_ADMIN_PASSWORD`: the server creates `admin` when no admin
   exists.
3. Bind machine users: `evalkit users add ci-bot --subject <sub>`, then
   `evalkit workspaces members add dev ci-bot --role writer`.
4. The first sign-in names the user after the part before `@` in
   `preferred_username`, else in `email`, in lowercase. If a user or
   workspace already has that name, evalkit adds the first free number
   (`ann1`, `ann2`, ...). To link an identity to an existing account
   instead, such as a password account that predates SSO, bind it before
   that person first signs in: `evalkit users bind <username> --subject <sub>`.
5. Revoke: `evalkit users archive <username>` blocks the next request. The
   IdP's own revocation takes effect within one access token lifetime.

## Troubleshooting

| Answer | Meaning | What to change |
| --- | --- | --- |
| The login page shows `Failed to fetch` after **Continue with <provider>** | The browser could not reach the IdP's discovery document or token endpoint | Allow evalkit's origin in the IdP's CORS settings |
| 401 `not registered: subject <sub> has no user` | No user for that identity | A person signs in through the UI or `evalkit login`; a machine's admin runs `users add --subject <sub>` |
| 401 `invalid access token: Audience doesn't match` | The token was not issued for evalkit | Fix the audience registration; check `authorize_params` or the scope |
| 401 `invalid access token: Invalid issuer` | `auth.oidc.issuer` differs from the token's `iss` | Copy the issuer from the discovery document, without a trailing slash |
| 401 `invalid access token: Signature has expired` | Expired access token | The clients refresh automatically; for the CLI, `evalkit login` again if the refresh token is gone |
| 401 `invalid access token: unknown signing key <kid>` | The IdP rotated keys and the token's key is not in the JWKS | The server refetches once a minute; if it persists, the token comes from another issuer |
| 401 `SSO is not configured on this server` | A JWT reached a server without `auth.oidc` | Configure `auth.oidc` or use password login |
| 401 `user is archived` | Archived in evalkit | `evalkit users restore <username>` |
| 403 `password login is for admins on this server` | `password_login: admin` | Sign in with SSO, or set `password_login: all` |
| 403 `password login is off on this server` | `password_login: none` | Sign in with SSO; CI uses its machine client |
| 403 `no tenant for this request` | A wrapper's `resolve_tenant` found no tenant for the token | Have the IdP put the organisation in the token, CI's client credentials tokens included |
| 403 `access token lacks required scope <scope>` (on `/api` and register; `/mcp` says `insufficient_scope`) | `auth.oidc.required_scopes` names a scope the token does not carry | Grant the scope to the person or machine client at the IdP and add it to `auth.oidc.scopes`; sign in again |
| 503 `identity provider unreachable` | Discovery or the JWKS could not be fetched | Check the issuer URL and network egress from the server; it retries every 30 s |
| No "Continue with ..." button on the login page | `/api/auth/providers` has no `oidc` | `auth.oidc` is absent or the server was not restarted |
| `your login has expired; run evalkit login` | The refresh token was refused and nothing newer was on disk | `evalkit login --url <url>` again |
