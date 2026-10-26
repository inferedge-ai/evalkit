from collections.abc import Callable

import typer

from evalkit.cli.client import ApiClient

tokens_app = typer.Typer(
    no_args_is_help=True, help="Agent tokens for exporting traces from deployed agents."
)


def register(app: typer.Typer, client_factory: Callable[[], ApiClient]) -> None:
    app.add_typer(tokens_app, name="tokens")

    @tokens_app.command("create")
    def tokens_create(
        agent: str = typer.Option(
            ..., "--agent", help="myagent (yours) or ouragent@dev (needs write access there)."
        ),
    ):
        """Mint an agent token, scoped to the collector, and print it once.

        The token never expires and is bound to one agent record on the server:
        it can only export spans for that agent.

        The agent must exist first (evalkit agents create). You need write
        access to its workspace: your own, or a shared one where you are a
        writer. Non-admins list and revoke only their own tokens.

        Agents cannot be renamed. To replace one, archive it, create the new
        one, mint a token for it and revoke the old token.

        Example: evalkit tokens create --agent ouragent@dev
        """
        body = client_factory().post("/api/tokens", json={"agent": agent}).json()
        typer.echo(f"token {body['id']} for {body['agent']} (shown once):")
        typer.echo(body["token"])

    @tokens_app.command("list")
    def tokens_list():
        """List the agent tokens you may see. Example: evalkit tokens list"""
        for row in client_factory().get("/api/tokens").json():
            typer.echo(
                f"{row['id']:<6} {row['agent']:<32} by {row['created_by']:<16} "
                f"last used {row['last_used_at'] or 'never'}"
            )

    @tokens_app.command("revoke")
    def tokens_revoke(token_id: int):
        """Revoke an agent token. Example: evalkit tokens revoke 12"""
        client_factory().delete(f"/api/tokens/{token_id}")
        typer.echo(f"revoked {token_id}")
