# Tenancy foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the public evalkit schema and server a tenant concept with one default tenant, so that a wrapper can host several tenants later without a migration of its own, while a single installation sees no change.

**Architecture:** A `tenants` table and a `tenant_id` column on the six root tables (`users`, `workspaces`, `cases`, `llm_providers`, `trace_sources`, `fixture_blobs`). The tenant is a property of the acting user, carried on `Actor` and `WorkspaceRef`, bound into every store at construction, and stamped on every database session; a SQLAlchemy `do_orm_execute` listener adds `tenant_id = t` to every ORM statement that touches a root model, so a forgotten filter cannot read or change a foreign row. Five pre-tenant lookups (token by hash, agent token by hash, experiment by id, trace import by id, any-admin check) run with an explicit `ALL_TENANTS` sentinel. Blob objects move under `tenants/<id>/` through a prefixing blob store and a one-time `evalkit blobs relocate` command.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async, Alembic, FastAPI, Postgres 17 (testcontainers in tests), obstore, typer.

**Spec:** `docs/superpowers/specs/2026-11-03-oidc-sso-and-tenancy-design.md`, sections "Data model", "Tenant threading and access checks", and the tenancy rows of "Docs, changelog, versioning and tests". Interface names come from `docs/superpowers/plans/2026-11-04-oidc-sso-and-tenancy/00-overview.md`, "Shared interface contract".

## Global Constraints

- Run every tool through `uv`: `uv run pytest`, `uv run ruff check`, `uv run ruff format`, `uv run mypy src/evalkit`. Tests need Docker (testcontainers Postgres) or `EVALKIT_TEST_PG_URL` pointing at a Postgres server.
- No new docstrings or inline comments, except the removal note the spec requires on the relocate command: `# TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/` (fill in the real versions at release time; keep the sentence shape).
- f-strings everywhere; `X | None`, never `Optional`; no local imports; no `try`/`except`; new functions start with a verb (`find_token`, `resolve_tenant`, `build_tenant_stores`).
- `tenant_id` columns have **no database default and no model default**. Every insert of a root row passes it explicitly.
- Store constructors take `tenant_id: int` as the **second positional argument**, after the session factory.
- The sentinel is `evalkit.db.session.ALL_TENANTS`; only the code this plan names may open a session with it.
- The client, CLI and SDK never see a tenant: blob keys they compute stay as they are today; the server prefixes them.
- Stage explicit paths with `git add <paths>`; never `git add -A`. Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Do not bump `pyproject.toml`'s version; the release PR does that.
- The `evalkit` CLI, the SDK and the public HTTP API keep their current shapes except where a task says otherwise (the workspace payload gains `tenant_id`).

## File structure

| File | Responsibility after this plan |
| --- | --- |
| `src/evalkit/db/models.py` | `Tenant` model; `tenant_id` on the six root models and `case_version_files`; identity columns and `kind` on `User`; `ROOT_MODELS` tuple; `ServerSettings` removed |
| `src/evalkit/db/alembic/versions/0016_tenants.py` | The migration: tenants row 1, backfill, per-tenant keys, fixture blob key change, `server_settings` folded in |
| `src/evalkit/db/session.py` | `ALL_TENANTS`, `tenant_session`, `register_tenant_guard`, `scope_to_tenant`; `make_session_factory` registers the guard |
| `src/evalkit/db/auth.py` (new) | `find_token`: the pre-tenant token lookup |
| `src/evalkit/core/tenant.py` (new) | `resolve_tenant` |
| `src/evalkit/core/access.py` | `tenant_id` on `Actor` and `WorkspaceRef`; `same_tenant`; every `can_*` guarded |
| `src/evalkit/core/blob.py` | `BlobStore.has_any`; `TenantBlobStore` prefixing wrapper; `TENANT_PREFIX`, `tenant_prefix` |
| `src/evalkit/core/relocate.py` (new) | `relocate_blobs`, `old_prefix_objects_remain` |
| `src/evalkit/db/*.py` stores | Each bound to a tenant; per-tenant filters on name lookups; `tenant_id` on root inserts |
| `src/evalkit/api/access.py` | `TenantStores`, `build_tenant_stores`, `tenant_stores`, `StoresDep`, `workspace_ref(stores, name)`, `resolve_user(stores, username)`, `load_actor` |
| `src/evalkit/api/auth.py` | `current_user` through `find_token`; `bearer_login` over the factory; `bootstrap_admin` through `any_admin_exists` |
| `src/evalkit/api/deps.py` | `SessionDep` scoped to the current user's tenant; `scoped_runner_deps` |
| `src/evalkit/api/app.py` | `create_app(..., resolve_tenant=...)`; store singletons replaced by `build_tenant_stores`; startup relocation check |
| `src/evalkit/cli/server_commands.py`, `src/evalkit/cli/main.py` | `evalkit blobs relocate` |
| `tests/pg.py` | `session_factory` yields a tenant-1 factory; `raw_session_factory` for guard tests |
| `tests/db/test_tenancy.py` (new) | Guard, cross-tenant and opener-set tests |

---

### Task 1: Models and migration `0016`

**Files:**
- Modify: `src/evalkit/db/models.py`
- Create: `src/evalkit/db/alembic/versions/0016_tenants.py`
- Modify: `tests/db/test_migration_roundtrip.py:17`
- Create: `tests/db/test_migration_0016.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `models.Tenant` (`id`, `name`, `default_judge`); `tenant_id` on `User`, `Workspace`, `Case`, `LlmProvider`, `TraceSource`, `FixtureBlob` (part of its primary key) and `CaseVersionFile`; `User.issuer`, `User.subject`, `User.email`, nullable `User.password_hash`, hybrid `User.kind`; `models.ROOT_MODELS: tuple[type[Base], ...]`; `models.ServerSettings` removed; head revision `0016`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/db/test_migration_0016.py`:

```python
import json

import pytest
from sqlalchemy import create_engine, inspect, text

from evalkit.db.session import downgrade_migrations, head_revision, run_migrations


def _exec(url: str, statements: list[str]) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for statement in statements:
            conn.execute(text(statement))
    engine.dispose()


def _rows(url: str, sql: str) -> list[tuple]:
    engine = create_engine(url)
    with engine.connect() as conn:
        found = list(conn.execute(text(sql)).all())
    engine.dispose()
    return found


def _seed_at_0015(url: str) -> None:
    judge = json.dumps({"base_url": "https://llm.example", "model": "m"})
    _exec(
        url,
        [
            "INSERT INTO users (id, username, password_hash, is_admin) VALUES (1, 'admin', 'x', true)",
            "INSERT INTO workspaces (id, name, owner_user_id) VALUES (1, 'admin', 1)",
            "INSERT INTO workspaces (id, name) VALUES (2, 'dev')",
            "INSERT INTO cases (id, case_id) VALUES (1, 'q-1')",
            "INSERT INTO llm_providers (id, name, base_url) VALUES (1, 'p', 'https://llm.example')",
            "INSERT INTO trace_sources (id, name, provider, config) VALUES (1, 's', 'phoenix', '{}')",
            f"INSERT INTO fixture_blobs (sha256, size) VALUES ('{'a' * 64}', 10)",
            f"INSERT INTO server_settings (id, default_judge) VALUES (1, '{judge}')",
        ],
    )


def test_0016_backfills_tenant_1_and_folds_server_settings(fresh_db_url):
    run_migrations(fresh_db_url, "0015")
    _seed_at_0015(fresh_db_url)
    run_migrations(fresh_db_url, "0016")
    assert head_revision() == "0016"
    assert _rows(fresh_db_url, "SELECT id, name FROM tenants") == [(1, "default")]
    [(judge,)] = _rows(fresh_db_url, "SELECT default_judge->>'model' FROM tenants WHERE id = 1")
    assert judge == "m"
    for table in ("users", "workspaces", "cases", "llm_providers", "trace_sources", "fixture_blobs"):
        assert _rows(fresh_db_url, f"SELECT DISTINCT tenant_id FROM {table}") == [(1,)]
    engine = create_engine(fresh_db_url)
    inspector = inspect(engine)
    assert "server_settings" not in inspector.get_table_names()
    assert inspector.get_pk_constraint("fixture_blobs")["constrained_columns"] == [
        "tenant_id",
        "sha256",
    ]
    user_columns = {c["name"]: c for c in inspector.get_columns("users")}
    assert user_columns["password_hash"]["nullable"] is True
    assert {"issuer", "subject", "email"} <= set(user_columns)
    index_names = {i["name"] for i in inspector.get_indexes("users")}
    assert "uq_users_tenant_subject" in index_names
    engine.dispose()


def test_0016_refuses_to_downgrade_with_a_second_tenant(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _exec(fresh_db_url, ["INSERT INTO tenants (id, name) VALUES (2, 'other')"])
    with pytest.raises(RuntimeError, match="tenant"):
        downgrade_migrations(fresh_db_url, "0015")


def test_0016_downgrades_cleanly_with_one_tenant(fresh_db_url):
    run_migrations(fresh_db_url, "0015")
    _seed_at_0015(fresh_db_url)
    run_migrations(fresh_db_url, "0016")
    downgrade_migrations(fresh_db_url, "0015")
    [(judge,)] = _rows(fresh_db_url, "SELECT default_judge->>'model' FROM server_settings")
    assert judge == "m"
    run_migrations(fresh_db_url, "0016")
```

If the downgrade refusal arrives wrapped in an alembic exception rather than as the `RuntimeError` the migration raises, widen the `pytest.raises` type to the wrapper and keep the `match`; do not replace it with `try`/`except`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/db/test_migration_0016.py -v`
Expected: FAIL. `run_migrations(fresh_db_url, "0016")` raises `alembic.util.exc.CommandError: Can't locate revision identified by '0016'`.

- [ ] **Step 3: Change the models**

In `src/evalkit/db/models.py`, add `and_` to the `sqlalchemy` import (the list already has `case`, `func`, `text`) and make these edits.

Add the `Tenant` model right after `AuditMixin`:

```python
class Tenant(AuditMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("name", name="uq_tenants_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    default_judge: Mapped[dict | None] = mapped_column(JSONB, default=None)
```

Replace the `User` class:

```python
class User(AuditMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),
        Index(
            "uq_users_tenant_subject",
            "tenant_id",
            "issuer",
            "subject",
            unique=True,
            postgresql_where=text("subject IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    username: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issuer: Mapped[str | None] = mapped_column(Text, default=None)
    subject: Mapped[str | None] = mapped_column(Text, default=None)
    email: Mapped[str | None] = mapped_column(Text, default=None)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @hybrid_property
    def kind(self) -> str:
        machine = self.subject is not None and self.email is None and self.password_hash is None
        return "machine" if machine else "human"

    @kind.inplace.expression
    @classmethod
    def _kind_expression(cls) -> ColumnElement[str]:
        return case(
            (
                and_(
                    cls.subject.is_not(None), cls.email.is_(None), cls.password_hash.is_(None)
                ),
                "machine",
            ),
            else_="human",
        )
```

In `Workspace`, replace the `__table_args__` and add the column:

```python
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_workspaces_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
```

In `LlmProvider`:

```python
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_llm_providers_tenant_name"),
        UniqueConstraint("tenant_id", "base_url", name="uq_llm_providers_tenant_base_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
```

Delete the whole `ServerSettings` class.

In `TraceSource`:

```python
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_trace_sources_tenant_name"),
        check_in("provider", TraceProvider, "ck_trace_sources_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
```

In `Case`:

```python
class Case(AuditMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id", name="uq_cases_tenant_case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[str] = mapped_column(String(255))
```

Replace `FixtureBlob` and `CaseVersionFile`:

```python
class FixtureBlob(AuditMixin, Base):
    __tablename__ = "fixture_blobs"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)


class CaseVersionFile(AuditMixin, Base):
    __tablename__ = "case_version_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sha256"],
            ["fixture_blobs.tenant_id", "fixture_blobs.sha256"],
            name="fk_case_version_files_blob",
        ),
    )

    case_ref: Mapped[int] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    context: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
```

At the very end of the file add:

```python
ROOT_MODELS: tuple[type[Base], ...] = (
    User,
    Workspace,
    Case,
    LlmProvider,
    TraceSource,
    FixtureBlob,
)
```

- [ ] **Step 4: Generate the migration with Alembic, then replace its body**

The repo has no `alembic.ini`; `evalkit.db.session.run_migrations` builds the config in code. Create a throwaway database at the current head and autogenerate against the updated models:

```bash
createdb evalkit_autogen
uv run python - <<'EOF'
from pathlib import Path
from alembic import command
from alembic.config import Config
import evalkit.db.session as s

url = "postgresql+psycopg://localhost:5432/evalkit_autogen"
cfg = Config()
cfg.set_main_option("script_location", str(Path(s.__file__).parent / "alembic"))
cfg.set_main_option("sqlalchemy.url", url)
command.upgrade(cfg, "0015")
command.revision(cfg, message="tenants", autogenerate=True, rev_id="0016")
EOF
dropdb evalkit_autogen
```

If your local Postgres needs a user and password, put them in the URL. This writes `src/evalkit/db/alembic/versions/0016_tenants.py` with a draft `upgrade()` that adds `tenant_id` as `nullable=False` with no default, which would fail on any existing row, and that cannot express the data moves. Keep the generated header (docstring, `revision`, `down_revision`, imports) and replace everything from `def upgrade()` down with:

```python
ROOT_TABLES = ["users", "workspaces", "cases", "llm_providers", "trace_sources", "fixture_blobs"]

OLD_UNIQUES = {
    "users": ["uq_users_username"],
    "workspaces": ["uq_workspaces_name"],
    "cases": ["uq_cases_case_id"],
    "llm_providers": ["uq_llm_providers_name", "uq_llm_providers_base_url"],
    "trace_sources": ["uq_trace_sources_name"],
}

NEW_UNIQUES = {
    "users": [("uq_users_tenant_username", ["tenant_id", "username"])],
    "workspaces": [("uq_workspaces_tenant_name", ["tenant_id", "name"])],
    "cases": [("uq_cases_tenant_case_id", ["tenant_id", "case_id"])],
    "llm_providers": [
        ("uq_llm_providers_tenant_name", ["tenant_id", "name"]),
        ("uq_llm_providers_tenant_base_url", ["tenant_id", "base_url"]),
    ],
    "trace_sources": [("uq_trace_sources_tenant_name", ["tenant_id", "name"])],
}


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("default_judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tenants_name"),
    )
    op.execute(
        "INSERT INTO tenants (id, name, default_judge) "
        "SELECT 1, 'default', (SELECT default_judge FROM server_settings WHERE id = 1)"
    )
    op.execute("SELECT setval('tenants_id_seq', 1)")
    op.drop_table("server_settings")

    for table in ROOT_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.execute(f"UPDATE {table} SET tenant_id = 1")
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_tenant", table, "tenants", ["tenant_id"], ["id"])

    for table, names in OLD_UNIQUES.items():
        for name in names:
            op.drop_constraint(name, table, type_="unique")
    op.drop_index("ix_users_username", table_name="users")
    for table, uniques in NEW_UNIQUES.items():
        for name, columns in uniques:
            op.create_unique_constraint(name, table, columns)

    op.add_column("case_version_files", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE case_version_files SET tenant_id = 1")
    op.alter_column("case_version_files", "tenant_id", nullable=False)
    op.drop_constraint(
        "case_version_files_sha256_fkey", "case_version_files", type_="foreignkey"
    )
    op.drop_constraint("fixture_blobs_pkey", "fixture_blobs", type_="primary")
    op.create_primary_key("fixture_blobs_pkey", "fixture_blobs", ["tenant_id", "sha256"])
    op.create_foreign_key(
        "fk_case_version_files_blob",
        "case_version_files",
        "fixture_blobs",
        ["tenant_id", "sha256"],
        ["tenant_id", "sha256"],
    )

    op.add_column("users", sa.Column("issuer", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("subject", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=True)
    op.create_index(
        "uq_users_tenant_subject",
        "users",
        ["tenant_id", "issuer", "subject"],
        unique=True,
        postgresql_where=sa.text("subject IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    tenants = bind.execute(sa.text("SELECT count(*) FROM tenants WHERE id <> 1")).scalar_one()
    if tenants:
        raise RuntimeError(
            f"{tenants} tenant(s) other than the default exist; 0016 cannot be downgraded"
        )
    op.drop_index("uq_users_tenant_subject", table_name="users")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=False)
    op.drop_column("users", "email")
    op.drop_column("users", "subject")
    op.drop_column("users", "issuer")

    op.drop_constraint("fk_case_version_files_blob", "case_version_files", type_="foreignkey")
    op.drop_constraint("fixture_blobs_pkey", "fixture_blobs", type_="primary")
    op.create_primary_key("fixture_blobs_pkey", "fixture_blobs", ["sha256"])
    op.create_foreign_key(
        "case_version_files_sha256_fkey",
        "case_version_files",
        "fixture_blobs",
        ["sha256"],
        ["sha256"],
    )
    op.drop_column("case_version_files", "tenant_id")

    for table, uniques in NEW_UNIQUES.items():
        for name, _ in uniques:
            op.drop_constraint(name, table, type_="unique")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_unique_constraint("uq_workspaces_name", "workspaces", ["name"])
    op.create_unique_constraint("uq_cases_case_id", "cases", ["case_id"])
    op.create_unique_constraint("uq_llm_providers_name", "llm_providers", ["name"])
    op.create_unique_constraint("uq_llm_providers_base_url", "llm_providers", ["base_url"])
    op.create_unique_constraint("uq_trace_sources_name", "trace_sources", ["name"])

    for table in ROOT_TABLES:
        op.drop_constraint(f"fk_{table}_tenant", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.create_table(
        "server_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("default_judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_server_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO server_settings (id, default_judge) "
        "SELECT 1, default_judge FROM tenants WHERE id = 1"
    )
    op.drop_table("tenants")
```

Make sure the file's imports are exactly `import sqlalchemy as sa`, `from alembic import op`, `from sqlalchemy.dialects import postgresql`. Two names in this file are Postgres's own: `ix_users_username` is the index the old `index=True, unique=True` column created, and `case_version_files_sha256_fkey` is the default name of the old foreign key. If `uv run pytest tests/db/test_migration_0016.py` fails on either name, read the real one with `\d users` and `\d case_version_files` in `psql` against a database at `0015` and use that. The old `users` unique came from `unique=True` on the column, whose constraint name is also `uq_users_username` only if Postgres named it so; if the drop fails, replace that entry with the name `\d users` shows.

- [ ] **Step 5: Update the roundtrip test's head**

In `tests/db/test_migration_roundtrip.py`, change `assert head_revision() == "0015"` to `assert head_revision() == "0016"`.

- [ ] **Step 6: Run the migration tests and the schema check**

Run: `uv run pytest tests/db/test_migration_0016.py tests/db/test_migration_roundtrip.py tests/db/test_schema_matches_models.py -v`
Expected: PASS. If `test_migrations_match_models` reports a diff, the models and the migration disagree; fix the migration to match the models, never the other way round.

- [ ] **Step 7: Commit**

```bash
git add src/evalkit/db/models.py src/evalkit/db/alembic/versions/0016_tenants.py tests/db/test_migration_0016.py tests/db/test_migration_roundtrip.py
git commit -m "Add tenants, tenant_id on root tables and user identity columns (migration 0016)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

The rest of the test suite does not pass after this commit: `UserStore.create` still inserts users with no `tenant_id`. The next tasks restore it. Do not run the whole suite until Task 9.

---

### Task 2: Tenant on `Actor` and `WorkspaceRef`, `same_tenant`, `resolve_tenant`

**Files:**
- Modify: `src/evalkit/core/access.py`
- Create: `src/evalkit/core/tenant.py`
- Modify: `src/evalkit/db/workspaces.py:33-45` (`to_ref`)
- Modify: `src/evalkit/api/access.py:18-29` (`load_actor`)
- Modify: `src/evalkit/api/app.py:162-173` (`create_app` signature) and `app.state`
- Modify: `tests/core/test_access.py`

**Interfaces:**
- Consumes: `User.tenant_id` (Task 1).
- Produces: `Actor(id, username, is_admin, tenant_id, roles)`; `WorkspaceRef(id, name, owner_user_id, archived, tenant_id, promotes_to=None, promoted_from=None)`; `same_tenant(actor, ws) -> bool`; `evalkit.core.tenant.resolve_tenant(request, claims) -> int`; `create_app(cfg, ..., resolve_tenant=resolve_tenant)` and `app.state.resolve_tenant`.

- [ ] **Step 1: Write the failing tests**

In `tests/core/test_access.py`, replace the four `WorkspaceRef(...)` constants and the `actor` helper, and add the cross-tenant tests at the end:

```python
AKSHAT = WorkspaceRef(1, "akshat", owner_user_id=10, archived=False, tenant_id=1)
BOB_WS = WorkspaceRef(2, "bob", owner_user_id=20, archived=False, tenant_id=1)
DEV = WorkspaceRef(3, "dev", owner_user_id=None, archived=False, tenant_id=1)
PROD = WorkspaceRef(4, "prod", owner_user_id=None, archived=False, tenant_id=1)
OTHER_DEV = WorkspaceRef(5, "dev", owner_user_id=None, archived=False, tenant_id=2)


def actor(uid, admin=False, tenant_id=1):
    return Actor(uid, f"u{uid}", admin, tenant_id, {})
```

Every other `Actor(...)` literal in that file gains `1` as the fourth argument, for example `Actor(20, "bob", False, 1, {3: frozenset({WorkspaceRole.WRITER})})`. Then append:

```python
def test_every_rule_refuses_a_workspace_in_another_tenant():
    admin = actor(99, admin=True)
    writer = Actor(20, "bob", False, 1, {5: frozenset({WorkspaceRole.WRITER, WorkspaceRole.REVIEWER})})
    assert not can_read(admin, OTHER_DEV)
    assert not can_write(admin, OTHER_DEV)
    assert not can_write(writer, OTHER_DEV)
    assert not can_review(writer, OTHER_DEV)
    assert not can_use_agent(admin, OTHER_DEV)
    assert not can_create_experiment(writer, OTHER_DEV)
    assert not can_change_experiment(admin, OTHER_DEV, created_by=99)


def test_same_tenant():
    assert same_tenant(actor(1), DEV)
    assert not same_tenant(actor(1, tenant_id=2), DEV)
```

Add `can_create_experiment` and `same_tenant` to the import list at the top. Create `tests/core/test_tenant.py`:

```python
from evalkit.core.tenant import resolve_tenant


def test_public_evalkit_has_one_tenant():
    assert resolve_tenant(None, None) == 1
    assert resolve_tenant(object(), {"sub": "anyone"}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_access.py tests/core/test_tenant.py -v`
Expected: FAIL. `WorkspaceRef.__init__() got an unexpected keyword argument 'tenant_id'` and `ModuleNotFoundError: No module named 'evalkit.core.tenant'`.

- [ ] **Step 3: Implement**

Create `src/evalkit/core/tenant.py`:

```python
from typing import Any

DEFAULT_TENANT_ID = 1


def resolve_tenant(request: Any, claims: dict | None) -> int:
    return DEFAULT_TENANT_ID
```

In `src/evalkit/core/access.py`, replace the two dataclasses and the rules:

```python
@dataclass(frozen=True)
class WorkspaceRef:
    id: int
    name: str
    owner_user_id: int | None
    archived: bool
    tenant_id: int
    promotes_to: int | None = None
    promoted_from: int | None = None

    @property
    def is_shared(self) -> bool:
        return self.owner_user_id is None

    @property
    def is_target(self) -> bool:
        return self.promoted_from is not None


@dataclass(frozen=True)
class Actor:
    id: int
    username: str
    is_admin: bool
    tenant_id: int
    roles: Mapping[int, frozenset[WorkspaceRole]]

    def has(self, ws: WorkspaceRef, role: WorkspaceRole) -> bool:
        return role in self.roles.get(ws.id, frozenset())


def same_tenant(actor: Actor, ws: WorkspaceRef) -> bool:
    return ws.tenant_id == actor.tenant_id


def can_read(actor: Actor, ws: WorkspaceRef) -> bool:
    return same_tenant(actor, ws)


def can_write(actor: Actor, ws: WorkspaceRef) -> bool:
    if not same_tenant(actor, ws):
        return False
    if not ws.is_shared:
        return ws.owner_user_id == actor.id
    return actor.is_admin or actor.has(ws, WorkspaceRole.WRITER)


def can_review(actor: Actor, ws: WorkspaceRef) -> bool:
    return same_tenant(actor, ws) and ws.is_shared and actor.has(ws, WorkspaceRole.REVIEWER)


def can_use_agent(actor: Actor, agent_ws: WorkspaceRef) -> bool:
    if not same_tenant(actor, agent_ws):
        return False
    if not agent_ws.is_shared:
        return agent_ws.owner_user_id == actor.id
    return True


def can_change_experiment(actor: Actor, exp_ws: WorkspaceRef, created_by: int | None) -> bool:
    return same_tenant(actor, exp_ws) and (can_write(actor, exp_ws) or created_by == actor.id)


def can_create_experiment(actor: Actor, exp_ws: WorkspaceRef) -> bool:
    if not same_tenant(actor, exp_ws):
        return False
    if not exp_ws.is_shared:
        return exp_ws.owner_user_id == actor.id
    return (
        actor.is_admin
        or actor.has(exp_ws, WorkspaceRole.WRITER)
        or actor.has(exp_ws, WorkspaceRole.REVIEWER)
    )
```

`experiment_workspace` is unchanged.

In `src/evalkit/db/workspaces.py`, `to_ref` passes the tenant:

```python
def to_ref(
    row: Workspace, promotes_to: int | None = None, promoted_from: int | None = None
) -> WorkspaceRef:
    return WorkspaceRef(
        row.id,
        row.name,
        row.owner_user_id,
        row.archived_at is not None,
        row.tenant_id,
        promotes_to=promotes_to,
        promoted_from=promoted_from,
    )
```

In `src/evalkit/api/access.py`, `load_actor` returns the tenant:

```python
    return Actor(
        user.id,
        user.username,
        user.is_admin,
        user.tenant_id,
        {k: frozenset(v) for k, v in roles.items()},
    )
```

In `src/evalkit/api/app.py`, import `from evalkit.core.tenant import resolve_tenant as default_resolve_tenant` with the other `evalkit.core` imports, add the parameter to `create_app`:

```python
def create_app(
    cfg: Config,
    *,
    blob_store: BlobStore | None = None,
    runner_deps: RunnerDeps | None = None,
    agents: AgentStore | None = None,
    session_factory=None,
    check_schema: bool = True,
    ui_dist: Path | None = None,
    holder: str | None = None,
    mcp_jwt_verifier: TokenVerifier | None = None,
    resolve_tenant: Callable[[Request, dict | None], int] = default_resolve_tenant,
) -> FastAPI:
```

(`from collections.abc import Callable` at the top), and next to `app.state.cfg = cfg` add `app.state.resolve_tenant = resolve_tenant`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_access.py tests/core/test_tenant.py -v && uv run mypy src/evalkit/core/access.py src/evalkit/core/tenant.py`
Expected: PASS, and mypy reports no issues for those two files. (`src/evalkit/api/app.py` does not type-check cleanly yet because `app.state` is untyped; that is the status quo.)

- [ ] **Step 5: Commit**

```bash
git add src/evalkit/core/access.py src/evalkit/core/tenant.py src/evalkit/db/workspaces.py src/evalkit/api/access.py src/evalkit/api/app.py tests/core/test_access.py tests/core/test_tenant.py
git commit -m "Carry the tenant on Actor and WorkspaceRef; every access rule refuses a cross-tenant pair

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The session guard

**Files:**
- Modify: `src/evalkit/db/session.py`
- Modify: `tests/pg.py:66-70` (`session_factory` fixture)
- Create: `tests/db/test_tenancy.py`

**Interfaces:**
- Consumes: `models.ROOT_MODELS` (Task 1).
- Produces: `evalkit.db.session.ALL_TENANTS`; `tenant_session(session_factory, tenant_id) -> AsyncIterator[AsyncSession]` (async context manager); `register_tenant_guard()`; `scope_to_tenant(state)`; `make_session_factory` registers the guard. Test fixtures: `session_factory` (tenant 1 by default), `raw_session_factory` (plain factory).

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_tenancy.py`:

```python
import pytest
from sqlalchemy import select, text, update

from evalkit.db.models import Tenant, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session


async def _add_tenant_2(raw_session_factory) -> None:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        s.add(User(tenant_id=2, username="eve", password_hash="x"))
        s.add(User(tenant_id=1, username="akshat", password_hash="x"))
        await s.commit()


async def test_a_session_without_a_tenant_fails_its_first_query(raw_session_factory):
    async with raw_session_factory() as s:
        with pytest.raises(RuntimeError, match="session has no tenant"):
            await s.execute(select(User))


async def test_a_scoped_session_cannot_read_a_foreign_root_row(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, 1) as s:
        names = (await s.scalars(select(User.username))).all()
        assert names == ["akshat"]
        eve_id = None
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        eve_id = await s.scalar(select(User.id).where(User.username == "eve"))
    async with tenant_session(raw_session_factory, 1) as s:
        assert await s.get(User, eve_id) is None


async def test_a_scoped_session_cannot_update_a_foreign_root_row(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, 1) as s:
        await s.execute(update(User).values(is_admin=True))
        await s.commit()
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        admins = (await s.scalars(select(User.username).where(User.is_admin.is_(True)))).all()
        assert admins == ["akshat"]


async def test_all_tenants_reads_everything(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        assert set((await s.scalars(select(User.username))).all()) == {"akshat", "eve"}


async def test_core_statements_are_untouched(raw_session_factory):
    async with tenant_session(raw_session_factory, 1) as s:
        assert await s.scalar(text("SELECT 1")) == 1


async def test_the_default_test_factory_is_tenant_1(session_factory):
    async with session_factory() as s:
        assert s.info["tenant_id"] == 1
        s.add(Workspace(tenant_id=1, name="dev"))
        await s.commit()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_tenancy.py -v`
Expected: FAIL with `ImportError: cannot import name 'ALL_TENANTS'` (and the `raw_session_factory` fixture is not found).

- [ ] **Step 3: Implement the guard**

In `src/evalkit/db/session.py`, add imports and the guard. New imports:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from evalkit.db.models import ROOT_MODELS
```

Then, above `make_session_factory`:

```python
ALL_TENANTS: Final = object()


def scope_to_tenant(state: ORMExecuteState) -> None:
    info = state.session.info
    if "tenant_id" not in info:
        raise RuntimeError("session has no tenant")
    tenant_id = info["tenant_id"]
    if tenant_id is ALL_TENANTS:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    for model in ROOT_MODELS:
        state.statement = state.statement.options(
            with_loader_criteria(
                model, lambda cls: cls.tenant_id == tenant_id, include_aliases=True
            )
        )


def register_tenant_guard() -> None:
    if not event.contains(Session, "do_orm_execute", scope_to_tenant):
        event.listen(Session, "do_orm_execute", scope_to_tenant)


@asynccontextmanager
async def tenant_session(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: int | object
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        session.info["tenant_id"] = tenant_id
        yield session
```

and make `make_session_factory` call `register_tenant_guard()` as its first line.

The listener is registered on the ORM's sync `Session` class, which is what an `AsyncSession` drives, and `AsyncSession.info` proxies to that session's `info`, so the stamp set in `tenant_session` is what the listener reads. The `lambda cls: cls.tenant_id == tenant_id` form is what SQLAlchemy's statement cache needs: the closed-over `tenant_id` becomes a bound parameter, so SQL compiles once per statement shape.

- [ ] **Step 4: Give the tests a tenant-1 factory by default**

In `tests/pg.py`, replace the `session_factory` fixture with a wrapper that stamps tenant 1 and add the raw one:

```python
class TenantFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession], tenant_id: int):
        self.factory = factory
        self.tenant_id = tenant_id
        self.kw = factory.kw

    def __call__(self):
        return tenant_session(self.factory, self.tenant_id)


@pytest.fixture
async def raw_session_factory(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = make_session_factory(db_url)
    yield factory
    await factory.kw["bind"].dispose()


@pytest.fixture
async def session_factory(raw_session_factory) -> TenantFactory:
    return TenantFactory(raw_session_factory, 1)
```

with `from evalkit.db.session import make_session_factory, run_migrations, tenant_session` as the import. `tenant_session` accepts the wrapper too: it calls `session_factory()`, which here opens a tenant-1 session, and then stamps the tenant it was given, so a store built on the wrapper with tenant 1 behaves as before and a pre-tenant lookup that passes `ALL_TENANTS` overrides the stamp. The 108 places in tests that do `async with session_factory() as s:` keep working unchanged: they now get a tenant-1 session.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/db/test_tenancy.py -v`
Expected: PASS, all six.

- [ ] **Step 6: Commit**

```bash
git add src/evalkit/db/session.py tests/pg.py tests/db/test_tenancy.py
git commit -m "Add the tenant session guard: every ORM statement on a root model is scoped to the session's tenant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Pre-tenant lookups and the authentication path

**Files:**
- Create: `src/evalkit/db/auth.py`
- Modify: `src/evalkit/db/users.py` (add `any_admin_exists`)
- Modify: `src/evalkit/db/experiments.py` (add `tenant_of_experiment`; `reap` opens an `ALL_TENANTS` session)
- Modify: `src/evalkit/db/trace_imports.py` (add `tenant_of_import`; `reap_imports` opens an `ALL_TENANTS` session)
- Modify: `src/evalkit/db/rekey.py` (`count_sealed`, `rekey_all` open `ALL_TENANTS` sessions)
- Modify: `src/evalkit/api/auth.py` (`current_user`, `bearer_login`, `collector_principal`, `login`, `download_ticket`, `logout`, `me`, `bootstrap_admin`)
- Modify: `src/evalkit/api/deps.py` (`get_session` scoped by the current user)
- Modify: `src/evalkit/api/mcp/auth.py` (`EvalkitTokenVerifier` over the factory; `tenant_id` claim)
- Create: `tests/db/test_auth_lookups.py`

**Interfaces:**
- Consumes: `tenant_session`, `ALL_TENANTS` (Task 3); `resolve_tenant` on `app.state` (Task 2).
- Produces: `evalkit.db.auth.find_token(session_factory, token_hash) -> tuple[AuthToken, int] | None`; `evalkit.db.users.any_admin_exists(session_factory) -> bool`; `evalkit.db.experiments.tenant_of_experiment(session_factory, experiment_id) -> int`; `evalkit.db.trace_imports.tenant_of_import(session_factory, import_id) -> int`; `evalkit.api.auth.bearer_login(session_factory, token) -> tuple[AuthToken, User]`; `Principal(user, agent_id, tenant_id)`; `SessionDep` now yields a session scoped to the current user's tenant; MCP access tokens carry `claims["tenant_id"]`.

Note on `UserStore`: this task calls `UserStore(session_factory, 1)` in `bootstrap_admin` and the `login` route reads `User` rows directly. `UserStore`'s own tenant-aware constructor arrives in Task 5; until then `bootstrap_admin` is the only caller that breaks, and Task 5 fixes it. Run only this task's test file here.

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_auth_lookups.py`:

```python
from datetime import UTC, datetime

from evalkit.contracts.enums import TokenKind
from evalkit.db.auth import find_token
from evalkit.db.models import Agent, AuthToken, Tenant, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.tokens import token_hash
from evalkit.db.users import any_admin_exists


async def _seed(raw_session_factory) -> tuple[int, int]:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        akshat = User(tenant_id=1, username="akshat", password_hash="x")
        eve = User(tenant_id=2, username="eve", password_hash="x")
        s.add_all([akshat, eve])
        await s.flush()
        ws = Workspace(tenant_id=2, name="eve", owner_user_id=eve.id)
        s.add(ws)
        await s.flush()
        agent = Agent(workspace_id=ws.id, name="bot", config={})
        s.add(agent)
        await s.flush()
        s.add(AuthToken(user_id=akshat.id, token_hash=token_hash("t-akshat"), kind=TokenKind.USER))
        s.add(
            AuthToken(
                user_id=eve.id,
                token_hash=token_hash("t-bot"),
                kind=TokenKind.AGENT,
                agent_id=agent.id,
            )
        )
        await s.commit()
        return akshat.id, eve.id


async def test_find_token_reads_the_tenant_through_the_user(raw_session_factory):
    akshat_id, _ = await _seed(raw_session_factory)
    found = await find_token(raw_session_factory, token_hash("t-akshat"))
    assert found is not None
    row, tenant_id = found
    assert (row.user_id, tenant_id) == (akshat_id, 1)


async def test_find_token_reads_an_agent_tokens_tenant_through_the_workspace(raw_session_factory):
    await _seed(raw_session_factory)
    found = await find_token(raw_session_factory, token_hash("t-bot"))
    assert found is not None
    assert found[1] == 2


async def test_find_token_unknown(raw_session_factory):
    assert await find_token(raw_session_factory, token_hash("nope")) is None


async def test_any_admin_exists_looks_across_tenants(raw_session_factory):
    assert not await any_admin_exists(raw_session_factory)
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        s.add(User(tenant_id=2, username="root", password_hash="x", is_admin=True))
        await s.commit()
    assert await any_admin_exists(raw_session_factory)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_auth_lookups.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalkit.db.auth'`.

- [ ] **Step 3: Write the lookups**

Create `src/evalkit/db/auth.py`:

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.db.models import Agent, AuthToken, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session


async def find_token(
    session_factory: async_sessionmaker[AsyncSession], token_hash: str
) -> tuple[AuthToken, int] | None:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        found = (
            await session.execute(
                select(AuthToken, func.coalesce(Workspace.tenant_id, User.tenant_id))
                .join(User, User.id == AuthToken.user_id)
                .outerjoin(Agent, Agent.id == AuthToken.agent_id)
                .outerjoin(Workspace, Workspace.id == Agent.workspace_id)
                .where(AuthToken.token_hash == token_hash)
            )
        ).one_or_none()
    if found is None:
        return None
    return found[0], int(found[1])
```

In `src/evalkit/db/users.py`, add after `verify_password`:

```python
async def any_admin_exists(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        found = await session.scalar(select(User.id).where(User.is_admin.is_(True)).limit(1))
    return found is not None
```

with `from evalkit.db.session import ALL_TENANTS, tenant_session` added to the imports.

In `src/evalkit/db/experiments.py`, add after `reap` and change `reap` to stamp the sentinel:

```python
async def reap(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    async with tenant_session(session_factory, ALL_TENANTS) as s:
        reaped = [
            exp_id
            for exp_id in await stale_active(s, datetime.now(UTC))
            if await transition_stale(s, exp_id)
        ]
        await s.commit()
    return reaped


async def tenant_of_experiment(
    session_factory: async_sessionmaker[AsyncSession], experiment_id: int
) -> int:
    async with tenant_session(session_factory, ALL_TENANTS) as s:
        tenant_id = await s.scalar(
            select(models.Workspace.tenant_id)
            .join(models.Dataset, models.Dataset.workspace_id == models.Workspace.id)
            .join(models.Experiment, models.Experiment.dataset_id == models.Dataset.id)
            .where(models.Experiment.id == experiment_id)
        )
    if tenant_id is None:
        raise UnknownExperiment(experiment_id)
    return tenant_id
```

In `src/evalkit/db/trace_imports.py`, the same shape:

```python
async def reap_imports(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        ...  # body unchanged


async def tenant_of_import(session_factory: async_sessionmaker[AsyncSession], import_id: int) -> int:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        tenant_id = await session.scalar(
            select(models.Workspace.tenant_id)
            .join(models.Agent, models.Agent.workspace_id == models.Workspace.id)
            .join(models.TraceImport, models.TraceImport.agent_id == models.Agent.id)
            .where(models.TraceImport.id == import_id)
        )
    if tenant_id is None:
        raise UnknownTraceImport(import_id)
    return tenant_id
```

In `src/evalkit/db/rekey.py`, both `count_sealed` and `rekey_all` replace `async with session_factory() as session:` with `async with tenant_session(session_factory, ALL_TENANTS) as session:`. These are the installation-wide paths the spec lists; nothing else in `src/` may use the sentinel except what Tasks 4 and 10 name.

- [ ] **Step 4: Rewire the authentication path**

Replace the middle of `src/evalkit/api/auth.py` (everything from `_valid_token` to `AdminUser`, and `bootstrap_admin`) with the version below. The file no longer imports `SessionDep`; `deps.py` will import `current_user`, so `auth.py` must not import `deps.py`.

```python
from evalkit.db.auth import find_token
from evalkit.db.session import tenant_session
from evalkit.db.users import (
    PASSWORD_MAX_BYTES,
    UserStore,
    any_admin_exists,
    hash_password,
    verify_password,
)
```

```python
def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return header.removeprefix("Bearer ")


def _check_live(row: AuthToken) -> None:
    expires = token_expiry(row)
    if expires is not None and expires < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="token expired")


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    if user.archived_at is not None:
        raise HTTPException(status_code=401, detail="user is archived")
    return user


async def bearer_login(
    session_factory: async_sessionmaker[AsyncSession], token: str
) -> tuple[AuthToken, User]:
    found = await find_token(session_factory, token_hash(token))
    if found is None:
        raise HTTPException(status_code=401, detail="invalid token")
    row, tenant_id = found
    _check_live(row)
    if row.kind == TokenKind.TICKET:
        raise HTTPException(status_code=401, detail="a download ticket is not a bearer token")
    if row.kind == TokenKind.AGENT:
        raise HTTPException(status_code=403, detail="agent tokens may only export traces")
    async with tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(AuthToken).where(AuthToken.id == row.id).values(last_used_at=datetime.now(UTC))
        )
        user = await _active_user(session, row.user_id)
        await session.commit()
    return row, user


def token_expiry(row: AuthToken) -> datetime | None:
    if row.expires_at is None:
        return None
    return row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)


# A download ticket is a single-use token carried in the query string so the
# browser can save a response straight to disk. URLs leak into logs and
# history, so a ticket only ever authorises a GET; a bearer header always wins.
async def current_user(request: Request) -> User:
    session_factory = request.app.state.session_factory
    ticket = request.query_params.get("ticket")
    if ticket is not None and request.method == "GET" and "authorization" not in request.headers:
        found = await find_token(session_factory, token_hash(ticket))
        if found is None:
            raise HTTPException(status_code=401, detail="invalid token")
        row, tenant_id = found
        _check_live(row)
        if row.kind != TokenKind.TICKET:
            raise HTTPException(status_code=401, detail="not a download ticket")
        async with tenant_session(session_factory, tenant_id) as session:
            await session.execute(delete(AuthToken).where(AuthToken.id == row.id))
            user = await _active_user(session, row.user_id)
            await session.commit()
        return user
    row, user = await bearer_login(session_factory, _bearer(request))
    request.state.token_expires_at = token_expiry(row)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass(frozen=True)
class Principal:
    user: User | None
    agent_id: int | None
    tenant_id: int


async def collector_principal(request: Request) -> Principal:
    session_factory = request.app.state.session_factory
    found = await find_token(session_factory, token_hash(_bearer(request)))
    if found is None:
        raise HTTPException(status_code=401, detail="invalid token")
    row, tenant_id = found
    _check_live(row)
    if row.kind == TokenKind.TICKET:
        raise HTTPException(status_code=401, detail="a download ticket is not a bearer token")
    async with tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(AuthToken).where(AuthToken.id == row.id).values(last_used_at=datetime.now(UTC))
        )
        if row.kind == TokenKind.AGENT:
            await session.commit()
            return Principal(user=None, agent_id=row.agent_id, tenant_id=tenant_id)
        user = await _active_user(session, row.user_id)
        await session.commit()
    return Principal(user=user, agent_id=None, tenant_id=tenant_id)


CollectorPrincipal = Annotated[Principal, Depends(collector_principal)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    tenant_id = request.app.state.resolve_tenant(request, None)
    if len(body.password.encode()) > PASSWORD_MAX_BYTES:
        raise HTTPException(
            status_code=400, detail=f"password is longer than {PASSWORD_MAX_BYTES} bytes"
        )
    async with tenant_session(request.app.state.session_factory, tenant_id) as session:
        user = (
            await session.execute(select(User).where(User.username == body.username))
        ).scalar_one_or_none()
        if user is None or user.password_hash is None:
            verify_password(body.password, _dummy_password_hash())
            raise HTTPException(status_code=401, detail="invalid username or password")
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid username or password")
        if user.archived_at is not None:
            raise HTTPException(status_code=401, detail="user is archived")
        ttl = timedelta(hours=request.app.state.cfg.auth.token_ttl_hours)
        token = await issue_token(session, user, ttl)
    return {"token": token, "username": user.username, "is_admin": user.is_admin}


@router.post("/download-ticket")
async def download_ticket(request: Request, user: CurrentUser) -> dict:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        await session.execute(delete(AuthToken).where(AuthToken.expires_at < datetime.now(UTC)))
        ticket = await issue_token(session, user, DOWNLOAD_TICKET_TTL, TokenKind.TICKET)
    return {"ticket": ticket}


@router.post("/logout")
async def logout(request: Request, user: CurrentUser) -> dict:
    token = _bearer(request)
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        await session.execute(delete(AuthToken).where(AuthToken.token_hash == token_hash(token)))
        await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(request: Request, user: CurrentUser) -> dict:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        rows = (
            await session.execute(
                select(Workspace.name, WorkspaceMember.role)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(WorkspaceMember.user_id == user.id)
                .order_by(Workspace.name, WorkspaceMember.role)
            )
        ).all()
        personal = await session.scalar(
            select(Workspace.name).where(
                Workspace.owner_user_id == user.id, Workspace.name == user.username
            )
        )
    roles: dict[str, list[str]] = {}
    for name, role in rows:
        roles.setdefault(name, []).append(role)
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "personal_workspace": personal,
        "roles": roles,
        "token_expires_at": getattr(request.state, "token_expires_at", None),
    }


async def bootstrap_admin(
    session_factory: async_sessionmaker[AsyncSession], password: str | None
) -> None:
    if await any_admin_exists(session_factory):
        return
    if not password:
        raise RuntimeError(
            "no admin user exists and EVALKIT_ADMIN_PASSWORD is not set; "
            "set it and start the server again to create the admin user"
        )
    await UserStore(session_factory, 1).create("admin", password, is_admin=True)
```

`issue_token` stays as it is (it takes a session). `_dummy_password_hash` stays. Add `update` to the `sqlalchemy` import. The `login` route's `resolve_tenant(request, None)` is the one call the wrapper overrides for password login.

In `src/evalkit/api/deps.py`, `get_session` becomes:

```python
from evalkit.api.auth import CurrentUser
from evalkit.db.session import tenant_session


async def get_session(request: Request, user: CurrentUser) -> AsyncIterator[AsyncSession]:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        yield session
```

In `src/evalkit/api/mcp/auth.py`, `EvalkitTokenVerifier.verify_token` becomes:

```python
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            row, user = await bearer_login(self._session_factory, token)
        except HTTPException:
            return None
        expires = token_expiry(row)
        return AccessToken(
            token=token,
            client_id=f"evalkit:{user.username}",
            scopes=[SCOPE_READ],
            expires_at=int(expires.timestamp()) if expires is not None else None,
            claims={"sub": user.username, "evalkit_user": user.username, "tenant_id": user.tenant_id},
        )
```

This `try`/`except HTTPException` already exists today and converts a refusal into "no token"; keep it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/db/test_auth_lookups.py -v && uv run ruff check src/evalkit/api/auth.py src/evalkit/db/auth.py`
Expected: PASS, four tests; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/evalkit/db/auth.py src/evalkit/db/users.py src/evalkit/db/experiments.py src/evalkit/db/trace_imports.py src/evalkit/db/rekey.py src/evalkit/api/auth.py src/evalkit/api/deps.py src/evalkit/api/mcp/auth.py tests/db/test_auth_lookups.py
git commit -m "Resolve a request's tenant from its token before any scoped work

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Scoped `UserStore` and `WorkspaceStore`

**Files:**
- Modify: `src/evalkit/db/users.py`
- Modify: `src/evalkit/db/workspaces.py`
- Modify: `tests/db/test_users.py`, `tests/db/test_workspaces.py`, `tests/db/test_promotion.py`, `tests/identity.py`, `tests/seed.py` (constructor calls gain `, 1`)

**Interfaces:**
- Consumes: `tenant_session` (Task 3).
- Produces: `UserStore(session_factory, tenant_id)`; `WorkspaceStore(session_factory, tenant_id)`; `name_taken(session, tenant_id, name)`; `user_id(session, tenant_id, username)`; `workspace_row(session, tenant_id, name)`; `WorkspaceStore.create_shared` inserts `tenant_id`; `add_member` and `_check_link` refuse cross-tenant pairs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_users.py`:

```python
async def test_user_store_is_bound_to_its_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    await UserStore(raw_session_factory, 1).create("akshat", "password")
    await UserStore(raw_session_factory, 2).create("akshat", "password")
    assert [u.username for u in await UserStore(raw_session_factory, 1).list()] == ["akshat"]
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        rows = (await s.execute(select(User.tenant_id, User.username))).all()
    assert sorted(rows) == [(1, "akshat"), (2, "akshat")]
    assert (await WorkspaceStore(raw_session_factory, 2).get_by_name("akshat")).tenant_id == 2
```

with `from evalkit.db.models import AuthToken, Tenant, User` and `from evalkit.db.session import ALL_TENANTS, tenant_session` at the top. Every existing `UserStore(session_factory)` in that file becomes `UserStore(session_factory, 1)` and every `WorkspaceStore(session_factory)` becomes `WorkspaceStore(session_factory, 1)`; the same in `tests/db/test_workspaces.py`, `tests/db/test_promotion.py`, `tests/identity.py` and `tests/seed.py` (`grep -rn "UserStore(session_factory)\|WorkspaceStore(session_factory)" tests` lists every site).

Append to `tests/db/test_workspaces.py`:

```python
async def test_member_grant_refuses_a_user_from_another_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    admin = await UserStore(raw_session_factory, 1).create("admin", "password", is_admin=True)
    await WorkspaceStore(raw_session_factory, 1).create_shared("dev", admin.id)
    await UserStore(raw_session_factory, 2).create("eve", "password")
    with pytest.raises(UnknownUser):
        await WorkspaceStore(raw_session_factory, 1).add_member(
            "dev", "eve", WorkspaceRole.WRITER, admin.id
        )
```

with the imports `from evalkit.db.models import Tenant`, `from evalkit.db.session import ALL_TENANTS, tenant_session`, `from evalkit.db.users import UnknownUser, UserStore` and `from evalkit.contracts.enums import WorkspaceRole` added as needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_users.py tests/db/test_workspaces.py -v`
Expected: FAIL. `UserStore.__init__() takes 2 positional arguments but 3 were given`, and the old tests fail at insert with `null value in column "tenant_id"`.

- [ ] **Step 3: Scope the user store**

In `src/evalkit/db/users.py`:

```python
async def name_taken(session: AsyncSession, tenant_id: int, name: str) -> bool:
    count = await session.scalar(
        select(func.count()).select_from(
            union_all(
                select(User.id).where(User.tenant_id == tenant_id, User.username == name),
                select(Workspace.id).where(Workspace.tenant_id == tenant_id, Workspace.name == name),
            ).subquery()
        )
    )
    return bool(count)


async def user_id(session: AsyncSession, tenant_id: int, username: str) -> int:
    found = await session.scalar(
        select(User.id).where(User.tenant_id == tenant_id, User.username == username)
    )
    if found is None:
        raise UnknownUser(username)
    return found


class UserStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: int):
        self._sf = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sf, self._tenant_id)

    async def create(self, username: str, password: str, *, is_admin: bool = False) -> User:
        check_name(username)
        check_new_password(password)
        async with self._session() as s:
            await lock_names(s)
            if await name_taken(s, self._tenant_id, username):
                raise NameTaken(username)
            user = User(
                tenant_id=self._tenant_id,
                username=username,
                password_hash=hash_password(password),
                is_admin=is_admin,
            )
            s.add(user)
            await s.flush()
            s.add(Workspace(tenant_id=self._tenant_id, name=username, owner_user_id=user.id))
            await s.commit()
            return user

    async def _get(self, s: AsyncSession, username: str) -> User:
        user = await s.scalar(
            select(User).where(User.tenant_id == self._tenant_id, User.username == username)
        )
        if user is None:
            raise UnknownUser(username)
        return user
```

Every other method replaces `async with self._sf() as s:` with `async with self._session() as s:` (`archive`, `restore`, `set_password`, `set_admin`, `list`). `_ensure_other_admin` adds `User.tenant_id == self._tenant_id` to its `where`, so "last admin" is per tenant. The module keeps `any_admin_exists` from Task 4.

- [ ] **Step 4: Scope the workspace store**

In `src/evalkit/db/workspaces.py`:

```python
async def workspace_row(session: AsyncSession, tenant_id: int, name: str) -> Workspace:
    row = await session.scalar(
        select(Workspace).where(Workspace.tenant_id == tenant_id, Workspace.name == name)
    )
    if row is None:
        raise UnknownWorkspace(name)
    return row
```

`_check_link` gains, as its first check:

```python
    if source.tenant_id != target.tenant_id:
        raise PromotionLinkError(f"{source.name} and {target.name} are in different tenants")
```

The store:

```python
class WorkspaceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: int):
        self._sf = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sf, self._tenant_id)

    async def get_by_name(self, name: str) -> WorkspaceRef:
        async with self._session() as s:
            return await load_ref(s, await workspace_row(s, self._tenant_id, name))

    async def get_by_id(self, workspace_id: int) -> WorkspaceRef:
        async with self._session() as s:
            row = await s.get(Workspace, workspace_id)
            if row is None:
                raise UnknownWorkspace(str(workspace_id))
            return await load_ref(s, row)

    async def personal_of(self, username: str) -> WorkspaceRef:
        async with self._session() as s:
            row = await s.scalar(
                select(Workspace)
                .join(User, User.id == Workspace.owner_user_id)
                .where(
                    Workspace.tenant_id == self._tenant_id,
                    User.username == username,
                    Workspace.name == username,
                )
            )
            if row is None:
                raise UnknownWorkspace(username)
            return to_ref(row)

    async def create_shared(self, name: str, actor_id: int | None) -> WorkspaceRef:
        check_name(name)
        async with self._session() as s:
            await lock_names(s)
            if await name_taken(s, self._tenant_id, name):
                raise NameTaken(name)
            row = Workspace(tenant_id=self._tenant_id, name=name)
            s.add(row)
            await s.commit()
            return to_ref(row)
```

`set_promotes_to`, `rename`, `set_archived`, `add_member`, `remove_member`, `members` and `list` keep their bodies with these three mechanical changes: `self._sf()` becomes `self._session()`; `workspace_row(s, name)` becomes `workspace_row(s, self._tenant_id, name)`; `name_taken(s, x)` becomes `name_taken(s, self._tenant_id, x)` and `user_id(s, username)` becomes `user_id(s, self._tenant_id, username)`. `list` adds `.where(Workspace.tenant_id == self._tenant_id)` to its query. Because `user_id` is now tenant-scoped, `add_member` with a username from another tenant raises `UnknownUser`, which is the same-tenant check the spec asks for on member grants; `get_by_id` of a foreign workspace returns `None` from `s.get` under the guard and raises `UnknownWorkspace`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/db/test_users.py tests/db/test_workspaces.py tests/db/test_promotion.py -v`
Expected: PASS. (`test_promotion.py` passes only if it constructs no other stores; if it uses `AgentStore` or `PgStore`, those fail until Tasks 6 and 8 and you may defer that file to Task 8's run.)

- [ ] **Step 6: Commit**

```bash
git add src/evalkit/db/users.py src/evalkit/db/workspaces.py tests/db/test_users.py tests/db/test_workspaces.py tests/db/test_promotion.py tests/identity.py tests/seed.py
git commit -m "Bind UserStore and WorkspaceStore to a tenant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Scoped agent, evaluator, mapping, span, token, key and session-reader stores

**Files:**
- Modify: `src/evalkit/db/agents.py`, `src/evalkit/db/evaluators.py`, `src/evalkit/db/mappings.py`, `src/evalkit/db/spans.py`, `src/evalkit/db/tokens.py`, `src/evalkit/db/keys.py`, `src/evalkit/db/sessions.py` (no change to `DbSessionReader` itself: it takes stores)
- Modify: `tests/db/test_agents.py`, `tests/db/test_evaluators.py`, `tests/db/test_mappings.py`, `tests/db/test_spans.py`, `tests/db/test_sessions.py`, `tests/db/test_stored_keys.py`, `tests/identity.py` (`seed_agent`, `seed_evaluators`)

**Interfaces:**
- Consumes: `workspace_row(session, tenant_id, name)` (Task 5); `WorkspaceRef.tenant_id` (Task 2).
- Produces: `AgentStore(session_factory, tenant_id)`, `RemoteEvaluatorStore(session_factory, tenant_id)`, `MappingStore(session_factory, tenant_id)`, `SpanStore(session_factory, tenant_id)`, `AgentTokens(session_factory, tenant_id)`, `KeyLoader(session_factory, tenant_id, box)`; `AgentStore.create(workspace: WorkspaceRef, name, ...)` and `RemoteEvaluatorStore.create(workspace: WorkspaceRef, name, ...)`; `agent_id_for(session, tenant_id, ref)`; `agent_definition(session, tenant_id, ref)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_agents.py`:

```python
async def test_agent_lookups_stay_inside_the_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    for tenant_id in (1, 2):
        admin = await UserStore(raw_session_factory, tenant_id).create("admin", "password", is_admin=True)
        dev = await WorkspaceStore(raw_session_factory, tenant_id).create_shared("dev", admin.id)
        await AgentStore(raw_session_factory, tenant_id).create(dev, "bot", harness_url="https://h")
    ours = await AgentStore(raw_session_factory, 1).get("bot@dev")
    theirs = await AgentStore(raw_session_factory, 2).get("bot@dev")
    assert ours.id != theirs.id
    assert [a.id for a in await AgentStore(raw_session_factory, 1).list_agents()] == [ours.id]
    with pytest.raises(UnknownAgent):
        await AgentStore(raw_session_factory, 1).get_by_id(theirs.id)
```

with imports `from evalkit.core.agents import UnknownAgent`, `from evalkit.db.models import Tenant`, `from evalkit.db.session import ALL_TENANTS, tenant_session`, `from evalkit.db.users import UserStore`, `from evalkit.db.workspaces import WorkspaceStore`.

In `tests/identity.py`, `seed_agent` and `seed_evaluators` resolve the workspace first:

```python
async def seed_agent(
    session_factory: async_sessionmaker[AsyncSession],
    name: str = "fake",
    workspace: str = "canonical",
    *,
    harness_url: str | None = FAKE_HARNESS_URL,
    trajectory_source: TrajectorySource = TrajectorySource.ATIF,
    settings: AgentSettings = FAKE_SETTINGS,
    mapping: dict | None = None,
) -> AgentDefinition:
    ws = await WorkspaceStore(session_factory, 1).get_by_name(workspace)
    return await AgentStore(session_factory, 1).create(
        ws,
        name,
        harness_url=harness_url,
        trajectory_source=trajectory_source,
        settings=settings,
        mapping=mapping,
        api_key_encrypted=TEST_BOX.seal(TEST_ENV["FAKE_AGENT_KEY"]) if harness_url else None,
    )


async def seed_evaluators(
    session_factory: async_sessionmaker[AsyncSession], workspace: str = "canonical"
) -> None:
    ws = await WorkspaceStore(session_factory, 1).get_by_name(workspace)
    store = RemoteEvaluatorStore(session_factory, 1)
    for name, settings in FAKE_EVALUATORS.items():
        await store.create(
            ws,
            name,
            EvaluatorKind.SCORE,
            settings,
            TEST_BOX.seal(TEST_ENV["CUSTOM_EVAL_KEY"]),
        )
```

and `seed_identity` constructs `LlmProviderStore(session_factory, 1)` (its store changes in Task 7; the constructor argument is added now so this file is edited once).

Every `AgentStore(session_factory)`, `RemoteEvaluatorStore(session_factory)`, `MappingStore(session_factory)`, `SpanStore(session_factory)`, `AgentTokens(session_factory)` and `KeyLoader(session_factory, box)` in the test files listed above gains the tenant: `AgentStore(session_factory, 1)`, `KeyLoader(session_factory, 1, box)`. Calls of `AgentStore(...).create("canonical", ...)` with a workspace name become `create(await WorkspaceStore(session_factory, 1).get_by_name("canonical"), ...)`; `grep -rn "\.create(\"" tests/db/test_agents.py tests/db/test_evaluators.py tests/db/test_stored_keys.py` lists them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_agents.py -v`
Expected: FAIL with `AgentStore.__init__() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Scope the agent store**

In `src/evalkit/db/agents.py`, the module functions take the tenant and the store binds it:

```python
async def agent_id_for(session: AsyncSession, tenant_id: int, ref: str) -> int:
    parsed = parse_slug(ref)
    agent_id = await session.scalar(
        select(models.Agent.id)
        .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
        .where(
            models.Workspace.tenant_id == tenant_id,
            models.Workspace.name == parsed.workspace,
            models.Agent.name == parsed.name,
        )
    )
    if agent_id is None:
        raise UnknownAgent(ref)
    return agent_id


async def _agent_row(
    session: AsyncSession, tenant_id: int, ref: str
) -> tuple[models.Agent, models.Workspace]:
    name, workspace = _ref(ref)
    found = (
        await session.execute(
            select(models.Agent, models.Workspace)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(
                models.Workspace.tenant_id == tenant_id,
                models.Agent.name == name,
                models.Workspace.name == workspace,
            )
        )
    ).one_or_none()
    if found is None:
        raise UnknownAgent(ref)
    return found[0], found[1]


async def agent_definition(session: AsyncSession, tenant_id: int, ref: str) -> AgentDefinition:
    return _definition(*await _agent_row(session, tenant_id, ref))


class AgentStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: int):
        self._sessions = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sessions, self._tenant_id)

    async def _row(self, session: AsyncSession, ref: str) -> tuple[models.Agent, models.Workspace]:
        return await _agent_row(session, self._tenant_id, ref)

    async def _agent_id(self, session: AsyncSession, name: str, workspace_id: int) -> int | None:
        return await session.scalar(
            select(models.Agent.id).where(
                models.Agent.workspace_id == workspace_id, models.Agent.name == name
            )
        )

    async def get(self, ref: str) -> AgentDefinition:
        async with self._session() as session:
            return await agent_definition(session, self._tenant_id, ref)

    async def find(self, ref: str) -> AgentDefinition | None:
        name, workspace = _ref(ref)
        async with self._session() as session:
            found = (
                await session.execute(
                    select(models.Agent, models.Workspace)
                    .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                    .where(
                        models.Workspace.tenant_id == self._tenant_id,
                        models.Agent.name == name,
                        models.Workspace.name == workspace,
                    )
                )
            ).one_or_none()
        return _definition(found[0], found[1]) if found is not None else None

    async def get_by_id(self, agent_id: int) -> AgentDefinition:
        async with self._session() as session:
            found = (
                await session.execute(
                    select(models.Agent, models.Workspace)
                    .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                    .where(
                        models.Workspace.tenant_id == self._tenant_id, models.Agent.id == agent_id
                    )
                )
            ).one_or_none()
        if found is None:
            raise UnknownAgent(str(agent_id))
        return _definition(found[0], found[1])

    async def create(
        self,
        workspace: WorkspaceRef,
        name: str,
        *,
        harness_url: str | None = None,
        trajectory_source: TrajectorySource = TrajectorySource.ATIF,
        repo_url: str | None = None,
        settings: AgentSettings | None = None,
        created_by: int | None = None,
        mapping: dict | None = None,
        api_key_encrypted: bytes | None = None,
    ) -> AgentDefinition:
        if "@" in name or not name:
            raise InvalidAgent(f"invalid agent name: {name!r}")
        if workspace.tenant_id != self._tenant_id:
            raise UnknownWorkspace(workspace.name)
        if trajectory_source is TrajectorySource.OTEL and mapping is None:
            raise InvalidAgent("otel agents need a mapping")
        if mapping is not None and trajectory_source is not TrajectorySource.OTEL:
            raise InvalidAgent("only otel agents take a mapping")
        _check_provides(harness_url, trajectory_source, settings)
        if mapping is not None:
            MappingDocument.model_validate(mapping)
        async with self._session() as session:
            ws = await session.get_one(models.Workspace, workspace.id)
            await lock_name(session, f"agent:{ws.id}", name)
            if await self._agent_id(session, name, ws.id) is not None:
                raise AgentExists(f"{name}@{ws.name}")
            row = models.Agent(
                workspace_id=ws.id,
                name=name,
                harness_url=harness_url,
                trajectory_source=trajectory_source.value,
                repo_url=repo_url,
                config=(settings or AgentSettings()).model_dump(),
                api_key_encrypted=api_key_encrypted,
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            if mapping is not None:
                await activate_document(session, row.id, mapping, created_by)
            await session.commit()
            return _definition(row, ws)
```

Imports: `from evalkit.core.access import WorkspaceRef`, `from evalkit.db.session import tenant_session`, and replace `from evalkit.db.workspaces import workspace_row` with `from evalkit.db.workspaces import UnknownWorkspace`. `edit`, `_switch_source`, `sealed_key`, `archive`, `restore` replace `self._sessions()` with `self._session()`. `list_agents` becomes:

```python
    async def list_agents(
        self, workspace: str | None = None, include_archived: bool = False
    ) -> list[AgentDefinition]:
        query = (
            select(models.Agent, models.Workspace)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(models.Workspace.tenant_id == self._tenant_id)
            .order_by(
                models.Workspace.owner_user_id.is_not(None),
                models.Workspace.name,
                models.Agent.name,
            )
        )
        if workspace is not None:
            query = query.where(models.Workspace.name == workspace)
        if not include_archived:
            query = query.where(models.Agent.archived_at.is_(None))
        async with self._session() as session:
            return [_definition(a, w) for a, w in (await session.execute(query)).all()]
```

- [ ] **Step 4: Scope the evaluator, mapping, span, token and key stores**

`src/evalkit/db/evaluators.py`: constructor `(session_factory, tenant_id)`, `_session()` helper, `self._sessions()` → `self._session()` everywhere. `_row` adds `models.Workspace.tenant_id == self._tenant_id` to its `where`. `create(self, workspace: WorkspaceRef, name, kind, settings, api_key_encrypted=None)` checks `workspace.tenant_id != self._tenant_id` (raise `UnknownWorkspace(workspace.name)`), loads the row with `session.get_one(models.Workspace, workspace.id)` and drops the `workspace_row` import. `sealed_keys(workspace)` and `list_evaluators` add `models.Workspace.tenant_id == self._tenant_id` to their `where`.

`src/evalkit/db/mappings.py`: constructor `(session_factory, tenant_id)` and `_session()`; every `self._sessions()` → `self._session()`. The `created_by` lookup at line 149 becomes `select(models.User.id).where(models.User.tenant_id == self._tenant_id, models.User.username == created_by)`. The by-`agent_id` queries (`list`, `_with_status`, `put_draft`, `activate`) join `models.Agent` and `models.Workspace` and add `models.Workspace.tenant_id == self._tenant_id`, so a foreign agent id reads as no mappings.

`src/evalkit/db/spans.py`: constructor `(session_factory, tenant_id)` and `_session()`; every `self._sessions()` → `self._session()`. `upsert(agent_id, ...)` first checks the agent belongs to the tenant:

```python
    async def _check_agent(self, session: AsyncSession, agent_id: int) -> None:
        found = await session.scalar(
            select(models.Agent.id)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(models.Workspace.tenant_id == self._tenant_id, models.Agent.id == agent_id)
        )
        if found is None:
            raise UnknownAgent(str(agent_id))
```

called at the top of `upsert`, `read_session`, `stream_session_spans`, `session_spans`, `list_sessions` and `list_unsessioned`. `UnknownAgent` comes from `evalkit.core.agents`.

`src/evalkit/db/tokens.py`: `AgentTokens(session_factory, tenant_id)` with `_session()`; `create`, `list` and `revoke` use it. `list` and `revoke` join `models.User` and add `models.User.tenant_id == self._tenant_id` to `where`, so an admin lists only their tenant's tokens.

`src/evalkit/db/keys.py`: `KeyLoader(session_factory, tenant_id, box)` builds `AgentStore(session_factory, tenant_id)`, `RemoteEvaluatorStore(session_factory, tenant_id)`, `LlmProviderStore(session_factory, tenant_id)` (Task 7 gives `LlmProviderStore` that constructor; add the argument now).

`src/evalkit/db/sessions.py`: unchanged. `DbSessionReader` takes already-scoped stores.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/db/test_agents.py tests/db/test_evaluators.py tests/db/test_mappings.py tests/db/test_spans.py tests/db/test_sessions.py tests/db/test_stored_keys.py -v`
Expected: PASS. If `test_stored_keys.py` constructs `LlmProviderStore`, run it again after Task 7.

- [ ] **Step 6: Commit**

```bash
git add src/evalkit/db/agents.py src/evalkit/db/evaluators.py src/evalkit/db/mappings.py src/evalkit/db/spans.py src/evalkit/db/tokens.py src/evalkit/db/keys.py tests/db/test_agents.py tests/db/test_evaluators.py tests/db/test_mappings.py tests/db/test_spans.py tests/db/test_sessions.py tests/db/test_stored_keys.py tests/identity.py
git commit -m "Bind agent, evaluator, mapping, span, token and key stores to a tenant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Scoped LLM provider, trace source, trace import and review stores; default judge on `tenants`

**Files:**
- Modify: `src/evalkit/db/llm_providers.py`, `src/evalkit/db/trace_sources.py`, `src/evalkit/db/trace_imports.py`, `src/evalkit/db/review.py`
- Modify: `src/evalkit/cli/server_commands.py:100-130` (`_has_llm_setup`, `_register_default_judge`)
- Modify: `tests/db/test_trace_sources.py`, `tests/db/test_trace_imports.py`, `tests/db/test_review.py`, `tests/api/test_llm_providers_api.py` as needed, `tests/native.py` (`ReviewLog(session_factory, 1)`)

**Interfaces:**
- Consumes: `Tenant.default_judge` (Task 1).
- Produces: `LlmProviderStore(session_factory, tenant_id)` with `default_judge` and `set_default_judge` on the tenant row; `TraceSourceStore(session_factory, tenant_id, box)`; `TraceImportStore(session_factory, tenant_id)` with `create` refusing a source or agent outside the tenant; `ReviewLog(session_factory, tenant_id)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_trace_sources.py`:

```python
async def test_default_judge_lives_on_the_tenant_row(session_factory):
    store = LlmProviderStore(session_factory, 1)
    await store.create("p", "https://llm.example", None)
    judge = JudgeSettings(base_url="https://llm.example", model="m")
    await store.set_default_judge(judge)
    assert await store.default_judge() == judge
    async with session_factory() as s:
        row = await s.get(Tenant, 1)
    assert row is not None and row.default_judge["model"] == "m"


async def test_trace_import_refuses_a_source_from_another_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    admin = await UserStore(raw_session_factory, 1).create("admin", "password", is_admin=True)
    dev = await WorkspaceStore(raw_session_factory, 1).create_shared("dev", admin.id)
    agent = await AgentStore(raw_session_factory, 1).create(dev, "bot", harness_url="https://h")
    other = await TraceSourceStore(raw_session_factory, 2, None).create(
        "phx", TraceProvider.PHOENIX, "https://phoenix.other", None
    )
    now = datetime.now(UTC)
    with pytest.raises(UnknownTraceSource):
        await TraceImportStore(raw_session_factory, 1).create(
            agent.id, other.id, "proj", now - timedelta(days=1), now, admin.id, "pod:test"
        )
```

with imports for `JudgeSettings` (`evalkit.contracts.settings`), `TraceProvider` (`evalkit.contracts.enums`), `Tenant`, `ALL_TENANTS`, `tenant_session`, `UserStore`, `WorkspaceStore`, `AgentStore`, `LlmProviderStore`, `TraceImportStore`, `UnknownTraceSource`, `datetime`, `timedelta`, `UTC`, `pytest`. Every `LlmProviderStore(session_factory)`, `TraceSourceStore(session_factory, box)`, `TraceImportStore(session_factory)` and `ReviewLog(session_factory)` across `tests/` gains the tenant as its second argument (`grep -rn "LlmProviderStore(\|TraceSourceStore(\|TraceImportStore(\|ReviewLog(" tests` lists them; `tests/native.py` and `tests/api/test_traces.py` and the two `tests/flows/` files are among them).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_trace_sources.py -v`
Expected: FAIL with `LlmProviderStore.__init__() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Implement**

`src/evalkit/db/llm_providers.py`: constructor `(session_factory, tenant_id)`, `_session()`, `self._sessions()` → `self._session()` everywhere. `_row`, `_check_free`, `active_by_base_url`, `sealed_key` and `list_providers` add `models.LlmProvider.tenant_id == self._tenant_id` to their `where`. `create` inserts `models.LlmProvider(tenant_id=self._tenant_id, name=name, base_url=base_url, api_key_encrypted=api_key_encrypted)` and takes `lock_name(session, f"llm-provider:{self._tenant_id}", name)`. The settings helper becomes the tenant row:

```python
    async def _tenant(self, session: AsyncSession) -> models.Tenant:
        return await session.get_one(models.Tenant, self._tenant_id)
```

and every `settings = await self._settings(session)` / `settings is not None and (settings.default_judge or {})` pair becomes `tenant = await self._tenant(session)` / `(tenant.default_judge or {})`. `default_judge` and `set_default_judge`:

```python
    async def default_judge(self) -> JudgeSettings | None:
        async with self._session() as session:
            tenant = await self._tenant(session)
        if tenant.default_judge is None:
            return None
        return JudgeSettings.model_validate(tenant.default_judge)

    async def set_default_judge(self, judge: JudgeSettings) -> JudgeSettings:
        if await self.active_by_base_url(judge.base_url) is None:
            raise UnknownLlmProvider(judge.base_url)
        async with self._session() as session:
            tenant = await self._tenant(session)
            tenant.default_judge = judge.model_dump()
            await session.commit()
        return judge
```

`models.Tenant` is not a root model for the guard (it has no `tenant_id`), and `session.get_one` by the store's own tenant id is the only read of it.

`src/evalkit/db/trace_sources.py`: `TraceSourceStore(session_factory, tenant_id, box)`, `_session()`, `self._sessions()` → `self._session()`. `_row`, the `create` existence check and `list_sources` add `models.TraceSource.tenant_id == self._tenant_id`; `create` inserts `tenant_id=self._tenant_id` and locks `f"trace-source:{self._tenant_id}"`.

`src/evalkit/db/trace_imports.py`: `TraceImportStore(session_factory, tenant_id)`, `_session()`, `self._sessions()` → `self._session()` in every method except `reap_imports` and `tenant_of_import` (Task 4). `_views()` adds `.where(models.Workspace.tenant_id == self._tenant_id)` by becoming a method `_views(self)`. `create` checks both sides before inserting:

```python
    async def _check_same_tenant(
        self, session: AsyncSession, agent_id: int, trace_source_id: int
    ) -> None:
        agent_tenant = await session.scalar(
            select(models.Workspace.tenant_id)
            .join(models.Agent, models.Agent.workspace_id == models.Workspace.id)
            .where(models.Agent.id == agent_id)
        )
        if agent_tenant != self._tenant_id:
            raise UnknownAgent(str(agent_id))
        source_tenant = await session.scalar(
            select(models.TraceSource.tenant_id).where(models.TraceSource.id == trace_source_id)
        )
        if source_tenant != self._tenant_id:
            raise UnknownTraceSource(str(trace_source_id))
```

called as the first statement inside `create`'s session. `UnknownAgent` is from `evalkit.core.agents`, `UnknownTraceSource` from `evalkit.db.trace_sources`. `job(import_id)` adds the tenant join: it selects `models.TraceImport` joined to `models.Agent` and `models.Workspace` with `models.Workspace.tenant_id == self._tenant_id`, so a foreign import id raises `UnknownTraceImport`.

`src/evalkit/db/review.py`: `ReviewLog(session_factory, tenant_id)`, `_session()`, `self._session_factory()` → `self._session()`. `_case_refs(session, tenant_id, case_ids)` and `_user_ids(session, tenant_id, names)` add the tenant to their `where` and every caller passes `self._tenant_id`; `_usernames` is by id and unchanged.

`src/evalkit/cli/server_commands.py`: `_has_llm_setup` and `_register_default_judge` construct `LlmProviderStore(factory, 1)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/db/test_trace_sources.py tests/db/test_trace_imports.py tests/db/test_review.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evalkit/db/llm_providers.py src/evalkit/db/trace_sources.py src/evalkit/db/trace_imports.py src/evalkit/db/review.py src/evalkit/cli/server_commands.py tests/db/test_trace_sources.py tests/db/test_trace_imports.py tests/db/test_review.py tests/native.py
git commit -m "Bind provider, trace source, trace import and review stores to a tenant; default judge on the tenant row

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Scoped cases, datasets, experiments, runs, promotion and `PgStore`

**Files:**
- Modify: `src/evalkit/db/cases.py`, `src/evalkit/db/experiments.py`, `src/evalkit/db/runs.py`, `src/evalkit/db/promotion.py`, `src/evalkit/db/fixture_blobs.py`, `src/evalkit/db/store.py`
- Modify: `tests/db/test_pg_cases.py`, `tests/db/test_pg_experiments.py`, `tests/db/test_pg_runs.py`, `tests/db/test_pg_fixtures.py`, `tests/db/test_pg_scorecard.py`, `tests/db/test_promotion.py`, `tests/seed.py`, `tests/native.py`

**Interfaces:**
- Consumes: `WorkspaceRef.tenant_id`; `workspace_row(session, tenant_id, name)`.
- Produces: `PgStore(session_factory, tenant_id)`; `get_dataset(session, tenant_id, workspace, name)`, `dataset_by_ref(session, tenant_id, ref)`, `find_dataset_row(session, tenant_id, workspace, name)`, `list_datasets(session, tenant_id, workspace=None)`, `case_ref_for(session, tenant_id, case_id)`, `case_refs_for(session, tenant_id, case_ids)`; `fixture_blobs.known_blobs(session, tenant_id, shas)`, `blob_size(session, tenant_id, sha256)`, `record_blob(session, tenant_id, sha256, size, uploaded_by)`; `experiments.get_experiment(session, exp_id)` returns only the session's tenant's experiment.

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_pg_cases.py`:

```python
async def test_case_ids_are_per_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    async with tenant_session(raw_session_factory, 1) as s:
        ours = await case_ref_for(s, 1, "q-1")
        await s.commit()
    async with tenant_session(raw_session_factory, 2) as s:
        theirs = await case_ref_for(s, 2, "q-1")
        await s.commit()
    assert ours != theirs
```

Append to `tests/db/test_pg_experiments.py`:

```python
async def test_experiment_by_id_is_not_found_from_another_tenant(session_factory, raw_session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await s.commit()
    await seed_agent(session_factory, "bot", "dev")
    exp = await PgStore(raw_session_factory, 1).create_experiment(
        NewExperiment(dataset="finance@dev", agent="bot@dev", runner="admin", repetitions=1, config=ExperimentConfig(judge=JudgeSettings(base_url=JUDGE_URL, model="m")))
    )
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    with pytest.raises(UnknownExperiment):
        await PgStore(raw_session_factory, 2).get_experiment(exp.id)
```

Match the `NewExperiment` construction to how the existing tests in that file build one (copy their keyword arguments; the point of the test is only the final `pytest.raises`). Imports as in the earlier tests, plus `from evalkit.db.store import PgStore`, `from evalkit.store.base import NewExperiment, UnknownExperiment`, `from tests.identity import seed_agent`, `from tests.seed import incoming, seed_world, sync`.

Every `PgStore(session_factory)` in `tests/` becomes `PgStore(session_factory, 1)` (`tests/native.py`, `tests/db/test_migrate_on_start.py`, `tests/core/test_runner.py`, `tests/sdk/test_run_experiment.py`, `tests/cli/test_cli.py`, `tests/flows/*`), and `record_blob(s, digest, len(data), uploaded_by)` in `tests/native.py` and `tests/db/test_pg_fixtures.py` becomes `record_blob(s, 1, digest, len(data), uploaded_by)`; `known_blobs(s, [...])` there becomes `known_blobs(s, 1, [...])`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/db/test_pg_cases.py -k per_tenant -v`
Expected: FAIL with `TypeError: case_ref_for() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Thread the tenant through the case and dataset helpers**

In `src/evalkit/db/cases.py`:

```python
def _dataset_query(tenant_id: int):
    return (
        select(models.Dataset, models.Workspace)
        .join(models.Workspace, models.Workspace.id == models.Dataset.workspace_id)
        .where(models.Workspace.tenant_id == tenant_id)
    )


async def get_dataset(session: AsyncSession, tenant_id: int, workspace: str, name: str) -> DatasetRow:
    rows = await _rows(
        session,
        _dataset_query(tenant_id).where(
            models.Workspace.name == workspace, models.Dataset.name == name
        ),
    )
    if not rows:
        raise UnknownDataset(f"{name}@{workspace}")
    return rows[0]


async def dataset_by_ref(session: AsyncSession, tenant_id: int, ref: str) -> DatasetRow:
    parsed = parse_slug(ref)
    return await get_dataset(session, tenant_id, parsed.workspace, parsed.name)


async def find_dataset_row(
    session: AsyncSession, tenant_id: int, workspace: str, name: str
) -> DatasetRow | None:
    rows = await _rows(
        session,
        _dataset_query(tenant_id).where(
            models.Workspace.name == workspace, models.Dataset.name == name
        ),
    )
    return rows[0] if rows else None


async def list_datasets(
    session: AsyncSession, tenant_id: int, workspace: str | None = None
) -> list[DatasetRow]:
    query = _dataset_query(tenant_id).where(models.Dataset.archived_at.is_(None))
    if workspace is not None:
        query = query.where(models.Workspace.name == workspace)
    return await _rows(
        session,
        query.order_by(
            models.Workspace.owner_user_id.is_not(None), models.Workspace.name, models.Dataset.name
        ),
    )


async def case_ref_for(session: AsyncSession, tenant_id: int, case_id: str) -> int:
    await session.execute(
        insert(models.Case)
        .values(tenant_id=tenant_id, case_id=case_id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "case_id"])
    )
    found = await session.scalar(
        select(models.Case.id).where(
            models.Case.tenant_id == tenant_id, models.Case.case_id == case_id
        )
    )
    assert found is not None
    return found


async def case_refs_for(
    session: AsyncSession, tenant_id: int, case_ids: list[str]
) -> dict[str, int]:
    if not case_ids:
        return {}
    unique = list(dict.fromkeys(case_ids))
    await session.execute(
        insert(models.Case)
        .values([{"tenant_id": tenant_id, "case_id": case_id} for case_id in unique])
        .on_conflict_do_nothing(index_elements=["tenant_id", "case_id"])
    )
    rows = await session.execute(
        select(models.Case.case_id, models.Case.id).where(
            models.Case.tenant_id == tenant_id, models.Case.case_id.in_(unique)
        )
    )
    return {case_id: ref for case_id, ref in rows.all()}
```

Inside `sync_dataset`, the call becomes `case_refs_for(session, workspace.tenant_id, [...])`, and `_replace_overlays` passes `workspace.tenant_id` to whichever of the two it calls (read it; it resolves overlay targets by `case_id`). `store_versions` writes `models.CaseVersionFile(..., tenant_id=<the case's tenant>)`: give `store_versions` and `store_version` a `tenant_id` parameter that `sync_dataset` passes as `workspace.tenant_id`, and include `tenant_id=tenant_id` in every `CaseVersionFile(...)` construction. `_usernames` is by id and unchanged. `create_dataset(session, workspace, name)` and `ensure_dataset` already take a `WorkspaceRef`; they need nothing.

`src/evalkit/db/fixture_blobs.py`:

```python
async def known_blobs(session: AsyncSession, tenant_id: int, shas: list[str]) -> set[str]:
    if not shas:
        return set()
    rows = await session.scalars(
        select(models.FixtureBlob.sha256).where(
            models.FixtureBlob.tenant_id == tenant_id, models.FixtureBlob.sha256.in_(shas)
        )
    )
    return set(rows.all())


async def blob_size(session: AsyncSession, tenant_id: int, sha256: str) -> int | None:
    return await session.scalar(
        select(models.FixtureBlob.size).where(
            models.FixtureBlob.tenant_id == tenant_id, models.FixtureBlob.sha256 == sha256
        )
    )


async def record_blob(
    session: AsyncSession, tenant_id: int, sha256: str, size: int, uploaded_by: int | None
) -> None:
    await session.execute(
        insert(models.FixtureBlob)
        .values(tenant_id=tenant_id, sha256=sha256, size=size, uploaded_by=uploaded_by)
        .on_conflict_do_nothing(index_elements=["tenant_id", "sha256"])
    )
```

`_uses` joins `models.FixtureBlob` on both `tenant_id` and `sha256`:

```python
        .join(
            models.FixtureBlob,
            (models.FixtureBlob.tenant_id == models.CaseVersionFile.tenant_id)
            & (models.FixtureBlob.sha256 == models.CaseVersionFile.sha256),
        )
```

`src/evalkit/db/experiments.py`: `_query()` becomes `_query(tenant_id)` adding `.where(HomeWorkspace.tenant_id == tenant_id)`; `get_experiment(session, exp_id)` and `list_experiments(session, ...)` read the tenant from the session: add at the top of the module

```python
def _tenant_of(session: AsyncSession) -> int:
    return session.info["tenant_id"]
```

and call `_query(_tenant_of(session))`. This is the one helper that reads the stamp rather than taking a parameter, because `get_experiment` is called from dozens of places with only a session, and the guard would already filter the joined `workspaces` row; the explicit `where` keeps the SQL readable. `_tenant_of` always sees an integer: the only `ALL_TENANTS` session in this module is `reap`, whose path (`stale_active`, `transition_stale`, `_compare_and_set`) never calls `get_experiment` or `_query`. Keep it that way; the opener test in Task 11 pins the set of sentinel users. `_agent(session, ref)` becomes `_agent(session, tenant_id, ref)` using `workspace_row(session, tenant_id, parsed.workspace)`; `_personal(session, username)` becomes `_personal(session, tenant_id, username)` with `models.User.tenant_id == tenant_id`; `create_experiment(session, req)` passes `_tenant_of(session)` to both and to `dataset_by_ref`. `set_model` and `pinned_cases` use `session.get(models.Experiment, exp_id)`, which has no tenant column: change both to `await get_experiment(session, exp_id)` first (which raises `UnknownExperiment` for a foreign id) and then `session.get`.

`src/evalkit/db/runs.py`: `_experiment(session, exp_id)` loads through `experiments.get_experiment` the same way (import it; it is already a sibling module). The `created_by` filter at line 409 adds `models.User.tenant_id == _tenant_of(session)` using the same helper imported from `evalkit.db.experiments`. `list_agent_runs`, `trajectory_keys`, `agent_run`, `runs_by_harness_ids`, `eval_context` and `experiment_run_ids` are by `agent_id`; each adds a join to `models.Agent` and `models.Workspace` with `models.Workspace.tenant_id == _tenant_of(session)` if it does not already join them.

`src/evalkit/db/promotion.py`: `_workspace(session, workspace_id)` uses `session.get_one(models.Workspace, id)`, which the guard filters; `_username` is by id. `plan_promotion` and `execute_promotion` call `dataset_by_ref`/`get_dataset`/`case_refs_for`; pass `workspace.tenant_id` from the `WorkspaceRef` they already hold.

`src/evalkit/db/store.py`:

```python
class PgStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: int):
        self._sf = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sf, self._tenant_id)
```

every `async with self._sf() as s:` → `async with self._session() as s:`; `dataset(ref)` → `cases.dataset_by_ref(s, self._tenant_id, ref)`; `cases_for` the same; `known_blobs(shas)` → `fixture_blobs.known_blobs(s, self._tenant_id, shas)`; `sync(...)`, `workspace_cases`, `overlays_on`, `has_dataset` already take a `WorkspaceRef` and need nothing beyond the session change. Add at the top of `sync` and `workspace_cases`: `if workspace.tenant_id != self._tenant_id: raise UnknownWorkspace(workspace.name)` (import from `evalkit.db.workspaces`).

`tests/seed.py`'s `sync` helper calls `sync_dataset(session, workspace=workspace, ...)`, which needs nothing new. `tests/native.py` `put_fixture` passes `1` to `record_blob`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/db -v`
Expected: PASS for every file under `tests/db`. This is the first point where the whole `tests/db` directory passes again.

- [ ] **Step 5: Commit**

```bash
git add src/evalkit/db/cases.py src/evalkit/db/experiments.py src/evalkit/db/runs.py src/evalkit/db/promotion.py src/evalkit/db/fixture_blobs.py src/evalkit/db/store.py tests/db tests/seed.py tests/native.py
git commit -m "Scope cases, datasets, experiments, runs and PgStore to a tenant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Blob keys under `tenants/<id>/`, `has_any`, and `evalkit blobs relocate`

**Files:**
- Modify: `src/evalkit/core/blob.py`
- Modify: `src/evalkit/store/blob.py` (`Obstore.has_any`)
- Modify: `src/evalkit/client/blob.py` (`HttpBlobStore.has_any`)
- Modify: `tests/fake_blob.py` (`DictBlobStore.has_any`)
- Create: `src/evalkit/core/relocate.py`
- Modify: `src/evalkit/cli/server_commands.py` (`relocate_blobs`), `src/evalkit/cli/main.py` (`blobs` command group)
- Modify: `src/evalkit/api/app.py` (startup refusal)
- Create: `tests/core/test_relocate.py`; modify `tests/store/test_blob.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `BlobStore.has_any(prefix) -> bool` on the protocol and all three implementations; `evalkit.core.blob.TENANT_PREFIX = "tenants/"`, `tenant_prefix(tenant_id) -> str`, `TenantBlobStore(inner, tenant_id)`; `evalkit.core.relocate.OLD_PREFIXES`, `relocate_blobs(blob, tenant_id=1) -> int`, `old_prefix_objects_remain(blob) -> bool`; `evalkit blobs relocate --config`; the server refuses to start while old-prefix objects remain. The four key functions (`trajectory_key`, `artifact_key`, `workspace_key`, `fixture_blob_key`) keep their current signatures: they produce logical keys, and `TenantBlobStore` turns them into storage keys.

Why a wrapper rather than tenant-taking key functions: the SDK and CLI compute these keys too (`src/evalkit/sdk/runner.py:370`, `src/evalkit/client/blob.py`), send them to `/api/experiments/{id}/blob/{key}`, and must never see a tenant. The server prefixes on the way in and strips on the way out, so the client layout is unchanged and `agent_runs.trajectory_key` keeps storing the logical key with no data migration.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_relocate.py`:

```python
from evalkit.core.blob import TenantBlobStore, fixture_blob_key, tenant_prefix, trajectory_key
from evalkit.core.relocate import old_prefix_objects_remain, relocate_blobs
from tests.fake_blob import DictBlobStore


async def test_tenant_blob_store_prefixes_every_key_and_strips_on_list():
    inner = DictBlobStore()
    scoped = TenantBlobStore(inner, 7)
    await scoped.put_object(trajectory_key(3, "run-1"), b"{}")
    assert set(inner.objects) == {"tenants/7/trajectories/3/run-1.json"}
    assert await scoped.get_object(trajectory_key(3, "run-1")) == b"{}"
    assert await scoped.object_exists(trajectory_key(3, "run-1"))
    assert await scoped.list_objects("trajectories/3/") == [("trajectories/3/run-1.json", 2)]
    assert await scoped.has_any("trajectories/")
    assert not await scoped.has_any("artifacts/")
    assert await scoped.object_url("a") == "memory://blob/tenants/7/a"
    await scoped.copy_object(trajectory_key(3, "run-1"), "x")
    assert "tenants/7/x" in inner.objects
    await scoped.delete_object("x")
    assert "tenants/7/x" not in inner.objects


async def test_two_tenants_do_not_see_each_others_fixtures():
    inner = DictBlobStore()
    sha = "a" * 64
    await TenantBlobStore(inner, 1).put_object(fixture_blob_key(sha), b"one")
    assert not await TenantBlobStore(inner, 2).object_exists(fixture_blob_key(sha))


async def test_relocate_moves_old_layout_under_tenant_1_and_is_idempotent():
    inner = DictBlobStore()
    await inner.put_object("fixtures/sha256/aa", b"f")
    await inner.put_object("trajectories/1/r.json", b"t")
    await inner.put_object("artifacts/1/r/out.md", b"a")
    await inner.put_object("workspaces/1/r.json", b"w")
    await inner.put_object("tenants/1/trajectories/1/already.json", b"x")
    assert await old_prefix_objects_remain(inner)
    moved = await relocate_blobs(inner)
    assert moved == 4
    assert set(inner.objects) == {
        f"{tenant_prefix(1)}fixtures/sha256/aa",
        f"{tenant_prefix(1)}trajectories/1/r.json",
        f"{tenant_prefix(1)}artifacts/1/r/out.md",
        f"{tenant_prefix(1)}workspaces/1/r.json",
        "tenants/1/trajectories/1/already.json",
    }
    assert not await old_prefix_objects_remain(inner)
    assert await relocate_blobs(inner) == 0


async def test_has_any_on_the_fake_store():
    inner = DictBlobStore()
    assert not await inner.has_any("p/")
    await inner.put_object("p/x", b"1")
    assert await inner.has_any("p/")
```

Append to `tests/store/test_blob.py`:

```python
async def test_has_any_stops_at_the_first_object(blob_store):
    assert not await blob_store.has_any("h/")
    await blob_store.put_object("h/one.txt", b"1")
    await blob_store.put_object("h/two.txt", b"2")
    assert await blob_store.has_any("h/")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_relocate.py tests/store/test_blob.py -v`
Expected: FAIL with `ImportError: cannot import name 'TenantBlobStore'`.

- [ ] **Step 3: Implement the wrapper and `has_any`**

In `src/evalkit/core/blob.py`, add `has_any` to the protocol and the wrapper after the key functions:

```python
class BlobStore(Protocol):
    ...  # existing methods

    async def has_any(self, prefix: str) -> bool: ...


TENANT_PREFIX = "tenants/"


def tenant_prefix(tenant_id: int) -> str:
    return f"{TENANT_PREFIX}{tenant_id}/"


class TenantBlobStore:
    def __init__(self, inner: BlobStore, tenant_id: int):
        self._inner = inner
        self._prefix = tenant_prefix(tenant_id)

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def put_object(self, key: str, data: bytes) -> None:
        await self._inner.put_object(self._key(key), data)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        await self._inner.put_stream(self._key(key), chunks)

    async def get_object(self, key: str) -> bytes:
        return await self._inner.get_object(self._key(key))

    async def get_stream(self, key: str) -> BlobStream:
        return await self._inner.get_stream(self._key(key))

    async def object_exists(self, key: str) -> bool:
        return await self._inner.object_exists(self._key(key))

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        found = await self._inner.list_objects(self._key(prefix))
        return [(key.removeprefix(self._prefix), size) for key, size in found]

    async def delete_object(self, key: str) -> None:
        await self._inner.delete_object(self._key(key))

    async def copy_object(self, source: str, destination: str) -> None:
        await self._inner.copy_object(self._key(source), self._key(destination))

    async def object_url(self, key: str) -> str:
        return await self._inner.object_url(self._key(key))

    async def has_any(self, prefix: str) -> bool:
        return await self._inner.has_any(self._key(prefix))
```

In `src/evalkit/store/blob.py`, add to `Obstore`:

```python
    async def has_any(self, prefix: str) -> bool:
        async for chunk in obstore.list(self._store, prefix=prefix):
            if chunk:
                return True
        return False
```

In `src/evalkit/client/blob.py`, add to `HttpBlobStore` (after `list_objects`):

```python
    async def has_any(self, prefix: str) -> bool:
        return bool(await self.list_objects(prefix))
```

In `tests/fake_blob.py`, add to `DictBlobStore`:

```python
    async def has_any(self, prefix: str) -> bool:
        return any(k.startswith(prefix) for k in self.objects)
```

- [ ] **Step 4: Implement relocation, the command and the startup refusal**

Create `src/evalkit/core/relocate.py`:

```python
# TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/
from evalkit.core.blob import BlobStore, tenant_prefix

OLD_PREFIXES = ("fixtures/", "trajectories/", "artifacts/", "workspaces/")


async def old_prefix_objects_remain(blob: BlobStore) -> bool:
    for prefix in OLD_PREFIXES:
        if await blob.has_any(prefix):
            return True
    return False


async def relocate_blobs(blob: BlobStore, tenant_id: int = 1) -> int:
    moved = 0
    for prefix in OLD_PREFIXES:
        for key, _ in await blob.list_objects(prefix):
            new_key = f"{tenant_prefix(tenant_id)}{key}"
            if not await blob.object_exists(new_key):
                await blob.copy_object(key, new_key)
            assert await blob.object_exists(new_key)
            await blob.delete_object(key)
            moved += 1
    return moved
```

In `src/evalkit/cli/server_commands.py`, add:

```python
# TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/
def relocate_blobs(config: Config) -> int:
    return asyncio.run(relocate.relocate_blobs(Obstore(config.blob.url)))
```

with `from evalkit.core import relocate` and `from evalkit.store.blob import Obstore` added to the imports.

In `src/evalkit/cli/main.py`, next to `db_app`:

```python
blobs_app = typer.Typer(no_args_is_help=True, help="Blob storage maintenance (server only).")
```

register it with `app.add_typer(blobs_app, name="blobs")` beside the other `add_typer` calls, and add after `db_rekey`:

```python
# TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/
@blobs_app.command("relocate")
def blobs_relocate(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml."),
):
    """Move blob objects from the pre-tenant layout under tenants/1/. Run once after
    upgrading to the release that added tenants; the server refuses to start until then.

    Example: evalkit blobs relocate --config /etc/evalkit/config.yaml
    """
    if server_commands is None:
        raise ApiError(
            "`evalkit blobs relocate` needs the server extra — pip install 'evalkit-py[server]'"
        )
    moved = server_commands.relocate_blobs(_server_config(config))
    typer.echo(f"relocated {moved} object(s) under tenants/1/")
```

(The CLI's command docstrings are its `--help` text and are the existing convention; they are not the inline comments the style rule forbids.)

In `src/evalkit/api/app.py`'s `lifespan`, right after the `verify_schema` block and before the `count_sealed` check:

```python
        # TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/
        if await old_prefix_objects_remain(resolved_blob_store):
            raise RuntimeError(
                "blob objects remain under the pre-tenant layout; run "
                "`evalkit blobs relocate --config <config>` once, then start the server again"
            )
```

with `from evalkit.core.relocate import old_prefix_objects_remain`.

- [ ] **Step 5: Fix the tests that seed blobs directly at old keys**

The server now reads through `TenantBlobStore(..., 1)` (wired in Task 10), and the startup check refuses old-layout objects. Tests that write to the fake store by hand must write the storage key:

- `tests/native.py` `put_fixture`: `await env.blob.put_object(f"{tenant_prefix(1)}{fixture_blob_key(digest)}", data)`.
- `tests/core/test_runner.py:219,232,540,734,763,1006`, `tests/sdk/test_run_experiment.py:200,295`, `tests/api/test_api.py:547`, `tests/client/test_local_run_integration.py:79-81`, `tests/flows/test_flow_04_fixtures.py:32`: every `env.blob.objects[<key>]` / `blob.objects[<key>]` read becomes `env.blob.objects[f"{tenant_prefix(1)}{<key>}"]`. Import `tenant_prefix` from `evalkit.core.blob` in each file.

Leave them failing until Task 10 wires the wrapper in; this step only edits them.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_relocate.py tests/store/test_blob.py -v && uv run mypy src/evalkit/core/blob.py src/evalkit/core/relocate.py src/evalkit/store/blob.py src/evalkit/client/blob.py`
Expected: PASS; mypy clean.

- [ ] **Step 7: Commit**

```bash
git add src/evalkit/core/blob.py src/evalkit/core/relocate.py src/evalkit/store/blob.py src/evalkit/client/blob.py src/evalkit/cli/server_commands.py src/evalkit/cli/main.py src/evalkit/api/app.py tests/fake_blob.py tests/core/test_relocate.py tests/store/test_blob.py tests/native.py tests/core/test_runner.py tests/sdk/test_run_experiment.py tests/api/test_api.py tests/client/test_local_run_integration.py tests/flows/test_flow_04_fixtures.py
git commit -m "Store blobs under tenants/<id>/ through a prefixing store; add evalkit blobs relocate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `TenantStores`, scoped dependencies, and route wiring

**Files:**
- Modify: `src/evalkit/api/access.py`, `src/evalkit/api/deps.py`, `src/evalkit/api/app.py`, `src/evalkit/api/server_keys.py`
- Modify every router: `agents.py`, `datasets.py`, `evaluators.py`, `experiments.py`, `fixtures.py`, `harness_check.py`, `imports.py`, `llm_providers.py`, `meta.py`, `otel.py`, `pull.py`, `review.py`, `runs.py`, `tokens.py`, `trace_imports.py`, `trace_sources.py`, `traces.py`, `users.py`, `workspaces.py`
- Modify: `src/evalkit/api/mcp/server.py`, `src/evalkit/api/mcp/tools.py`
- Modify: `tests/native.py` (`serve`), `tests/api/test_traces.py`, `tests/api/test_mcp.py`, `tests/flows/test_flow_14_import_from_trace_source.py`, `tests/flows/test_flow_17_agent_and_evaluator_admin.py`, `tests/cli/test_cli.py`, `tests/sdk/test_run_experiment.py`, `tests/db/test_trace_sources.py`, `tests/db/test_migrate_on_start.py`, `tests/api/test_cli_dist.py`, `tests/api/test_review_fixes.py`, `tests/flows/helpers_01_06.py`, `tests/flows/helpers_12_17.py` (`create_app` calls)

**Interfaces:**
- Consumes: every scoped store (Tasks 5–8), `TenantBlobStore` (Task 9), `SessionDep` scoped by user (Task 4).
- Produces: `evalkit.api.access.TenantStores`; `build_tenant_stores(state, tenant_id) -> TenantStores`; `tenant_stores(actor, request)` and `StoresDep`; `workspace_ref(stores, name)`; `resolve_user(stores, username)`; `create_app` loses its `agents=` parameter; `app.state.box`, `app.state.blob_store`, `app.state.runner_deps` (template), `app.state.session_factory`, `app.state.resolve_tenant`; the singletons `app.state.store`, `agents`, `spans`, `mappings`, `sessions`, `keys`, `tokens`, `trace_sources`, `llm_providers`, `trace_imports`, `review_log` are gone, and so are `PgStoreDep`, `AgentStoreDep`, `AgentTokensDep`, `SpanStoreDep`, `MappingStoreDep`, `SessionReaderDep`, `KeysDep`, `ReviewLogDep`, `RunnerDepsDep`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_tenant_stores.py`:

```python
from evalkit.api.access import build_tenant_stores
from evalkit.core.blob import tenant_prefix
from tests.native import api_env


async def test_tenant_stores_are_built_per_tenant(tmp_path, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        stores = build_tenant_stores(client.app.state, 1)
        assert stores.tenant_id == 1
        assert stores.store is not env.store
        assert (await stores.workspaces.get_by_name("canonical")).tenant_id == 1
        await stores.blob.put_object("probe", b"x")
        assert f"{tenant_prefix(1)}probe" in env.blob.objects
        assert stores.deps.store is stores.store
        assert stores.deps.blob_store is stores.blob
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_tenant_stores.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_tenant_stores'`.

- [ ] **Step 3: Write `TenantStores` and the dependencies**

Replace `src/evalkit/api/access.py` with:

```python
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import State

from evalkit.api.auth import CurrentUser
from evalkit.api.deps import SessionDep
from evalkit.config import Config
from evalkit.contracts.enums import WorkspaceRole
from evalkit.contracts.notes import PERSONAL_KEY_NOTE
from evalkit.core.access import Actor, WorkspaceRef, can_write
from evalkit.core.blob import BlobStore, TenantBlobStore
from evalkit.core.endpoints import check_endpoint
from evalkit.core.runner import RunnerDeps
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.keys import KeyLoader
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.mappings import MappingStore
from evalkit.db.models import User, WorkspaceMember
from evalkit.db.review import ReviewLog
from evalkit.db.session import tenant_session
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from evalkit.db.store import PgStore
from evalkit.db.tokens import AgentTokens
from evalkit.db.trace_imports import TraceImportStore
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore, load_ref, workspace_row


@dataclass(frozen=True)
class TenantStores:
    tenant_id: int
    session_factory: async_sessionmaker[AsyncSession]
    users: UserStore
    workspaces: WorkspaceStore
    agents: AgentStore
    evaluators: RemoteEvaluatorStore
    providers: LlmProviderStore
    sources: TraceSourceStore
    imports: TraceImportStore
    spans: SpanStore
    mappings: MappingStore
    tokens: AgentTokens
    keys: KeyLoader
    review: ReviewLog
    sessions: DbSessionReader
    store: PgStore
    blob: BlobStore
    deps: RunnerDeps

    def session(self):
        return tenant_session(self.session_factory, self.tenant_id)


def build_tenant_stores(state: State, tenant_id: int) -> TenantStores:
    sf = state.session_factory
    agents = AgentStore(sf, tenant_id)
    evaluators = RemoteEvaluatorStore(sf, tenant_id)
    spans = SpanStore(sf, tenant_id)
    mappings = MappingStore(sf, tenant_id)
    sessions = DbSessionReader(agents, spans, mappings, state.cfg.otel)
    store = PgStore(sf, tenant_id)
    keys = KeyLoader(sf, tenant_id, state.box)
    blob = TenantBlobStore(state.blob_store, tenant_id)
    template: RunnerDeps = state.runner_deps
    deps = replace(
        template,
        store=store,
        blob_store=blob,
        agents=agents,
        evaluators=evaluators,
        sessions=sessions,
        agent_keys=keys,
    )
    return TenantStores(
        tenant_id=tenant_id,
        session_factory=sf,
        users=UserStore(sf, tenant_id),
        workspaces=WorkspaceStore(sf, tenant_id),
        agents=agents,
        evaluators=evaluators,
        providers=LlmProviderStore(sf, tenant_id),
        sources=TraceSourceStore(sf, tenant_id, state.box),
        imports=TraceImportStore(sf, tenant_id),
        spans=spans,
        mappings=mappings,
        tokens=AgentTokens(sf, tenant_id),
        keys=keys,
        review=ReviewLog(sf, tenant_id),
        sessions=sessions,
        store=store,
        blob=blob,
        deps=deps,
    )


async def load_actor(session: AsyncSession, user: User) -> Actor:
    rows = (
        await session.execute(
            select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(
                WorkspaceMember.user_id == user.id
            )
        )
    ).all()
    roles: dict[int, set[WorkspaceRole]] = {}
    for ws_id, role in rows:
        roles.setdefault(ws_id, set()).add(WorkspaceRole(role))
    return Actor(
        user.id,
        user.username,
        user.is_admin,
        user.tenant_id,
        {k: frozenset(v) for k, v in roles.items()},
    )


async def current_actor(session: SessionDep, user: CurrentUser) -> Actor:
    return await load_actor(session, user)


ActorDep = Annotated[Actor, Depends(current_actor)]


def tenant_stores(actor: ActorDep, request: Request) -> TenantStores:
    return build_tenant_stores(request.app.state, actor.tenant_id)


StoresDep = Annotated[TenantStores, Depends(tenant_stores)]


async def workspace_ref(stores: TenantStores, name: str) -> WorkspaceRef:
    async with stores.session() as session:
        return await load_ref(session, await workspace_row(session, stores.tenant_id, name))


async def resolve_user(stores: TenantStores, username: str) -> User:
    async with stores.session() as session:
        user = await session.scalar(
            select(User).where(User.tenant_id == stores.tenant_id, User.username == username)
        )
    if user is None:
        raise HTTPException(status_code=404, detail=f"unknown user: {username}")
    return user


def require(allowed: bool, detail: str) -> None:
    if not allowed:
        raise HTTPException(status_code=403, detail=detail)


def ensure_active(ws: WorkspaceRef) -> None:
    if ws.archived:
        raise HTTPException(status_code=409, detail=f"workspace {ws.name} is archived")


async def writable_endpoint(
    stores: StoresDep,
    actor: ActorDep,
    cfg: Config,
    workspace: str,
    *,
    api_key: bool = False,
    url: str | None = None,
) -> WorkspaceRef:
    ws = await workspace_ref(stores, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    if api_key and not ws.is_shared:
        raise HTTPException(status_code=400, detail=PERSONAL_KEY_NOTE)
    if url is not None and ws.is_shared:
        await check_endpoint(url, allow_private=cfg.allow_private_endpoints)
    return ws


async def writable(stores: StoresDep, actor: ActorDep, workspace: str) -> WorkspaceRef:
    ws = await workspace_ref(stores, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    return ws
```

`writable` and `writable_endpoint` now return the ref so `create_agent` and `create_evaluator` can pass it to the stores' `create(workspace: WorkspaceRef, ...)`.

Replace `src/evalkit/api/deps.py` with:

```python
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.api.auth import CurrentUser
from evalkit.config import Config
from evalkit.db.session import tenant_session


def get_config(request: Request) -> Config:
    return request.app.state.cfg


def get_holder(request: Request) -> str:
    return request.app.state.holder


async def get_session(request: Request, user: CurrentUser) -> AsyncIterator[AsyncSession]:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        yield session


ConfigDep = Annotated[Config, Depends(get_config)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
HolderDep = Annotated[str, Depends(get_holder)]
```

- [ ] **Step 4: Rebuild `create_app`'s state**

In `src/evalkit/api/app.py`:

- Remove the `agents` parameter from `create_app`.
- Keep `resolved_blob_store`, `session_factory`, `box`. Delete `resolved_store`, `resolved_agents`, `resolved_spans`, `resolved_mappings`, `resolved_sessions`, `key_loader` and the `resolved_deps.sessions`/`agent_keys` fix-ups. The template deps become:

```python
    resolved_deps = (
        runner_deps
        if runner_deps is not None
        else RunnerDeps(
            cfg=cfg,
            store=PgStore(session_factory, 1),
            blob_store=resolved_blob_store,
            agents=AgentStore(session_factory, 1),
            evaluators=RemoteEvaluatorStore(session_factory, 1),
            credentials=NoKeysLoaded(),
        )
    )
```

`build_tenant_stores` replaces `store`, `blob_store`, `agents`, `evaluators`, `sessions` and `agent_keys` per tenant, so the template's values only carry `cfg`, `credentials`, the transports, `progress`, `python_evaluators` and `evaluation_concurrency`. Tests that inject `runner_deps` with `FakeAgents()` keep their fakes only through what `replace` does not override; `tests/db/test_migrate_on_start.py` does not call any route and is unaffected.

- `app.state` keeps `cfg`, `runner_deps` (the template), `holder`, `session_factory`, `tasks`, `import_tasks`, `resolve_tenant` and gains `box = box` and `blob_store = resolved_blob_store`. Delete the assignments of `review_log`, `store`, `agents`, `tokens`, `spans`, `mappings`, `sessions`, `trace_sources`, `keys`, `llm_providers`, `trace_imports`.
- The lifespan's shutdown lines become:

```python
        await experiments.interrupt_tasks(app.state.session_factory, app.state.tasks)
        await trace_imports.interrupt_imports(app.state.session_factory, app.state.import_tasks)
```

- `mount_mcp(app, mcp_jwt_verifier)` is unchanged; `build_mcp` reads `app.state.cfg` and `app.state.session_factory`, both still there.

In `src/evalkit/api/experiments.py`, `interrupt_tasks` builds a scoped store per experiment:

```python
async def interrupt_tasks(session_factory, registry: TaskRegistry) -> None:
    tracked = list(registry.values())
    for _, _, task in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, _, task in tracked), return_exceptions=True)
    results = await asyncio.gather(
        *(
            _fail_interrupted(session_factory, exp_id, holder)
            for exp_id, holder, task in tracked
            if task.cancelled()
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            LOGGER.warning("could not mark an interrupted experiment failed: %s", result)


async def _fail_interrupted(session_factory, exp_id: int, holder: str) -> None:
    store = PgStore(session_factory, await tenant_of_experiment(session_factory, exp_id))
    await store.transition(exp_id, ExperimentStatus.FAILED, holder, error=INTERRUPTED_ERROR)
```

with `from evalkit.db.experiments import tenant_of_experiment` and `from evalkit.db.store import PgStore`. In `src/evalkit/api/trace_imports.py`:

```python
async def interrupt_imports(session_factory, registry: ImportTasks) -> None:
    tracked = list(registry.items())
    for _, (_, task) in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, (_, task) in tracked), return_exceptions=True)
    for import_id, (holder, task) in tracked:
        if task.cancelled():
            store = TraceImportStore(session_factory, await tenant_of_import(session_factory, import_id))
            await store.finish(import_id, holder, TraceImportStatus.FAILED, IMPORT_STOPPED_ERROR)
```

with `from evalkit.db.trace_imports import TraceImportStore, TraceImportView, tenant_of_import`.

- [ ] **Step 5: Rewire every router**

The pattern is the same in every file: replace the removed dependencies with `stores: StoresDep` and read the store off it; pass `stores.tenant_id` to the session-level helpers that now take a tenant; call `workspace_ref(stores, name)` instead of `workspace_ref(session, name)`. The table gives the substitutions, then each file's specifics.

| Was | Becomes |
| --- | --- |
| `store: PgStoreDep` / `store.` | `stores: StoresDep` / `stores.store.` |
| `agents: AgentStoreDep` / `agents.` | `stores.agents.` |
| `deps: RunnerDepsDep` / `deps` | `stores.deps` |
| `spans: SpanStoreDep`, `mappings: MappingStoreDep`, `sessions: SessionReaderDep`, `tokens: AgentTokensDep`, `keys: KeysDep`, `review_log: ReviewLogDep` | `stores.spans`, `stores.mappings`, `stores.sessions`, `stores.tokens`, `stores.keys`, `stores.review` |
| `request.app.state.llm_providers` / `.trace_sources` / `.trace_imports` / `.keys` / `.sessions` / `.spans` / `.store` / `.agents` | `stores.providers` / `stores.sources` / `stores.imports` / `stores.keys` / `stores.sessions` / `stores.spans` / `stores.store` / `stores.agents` |
| `UserStore(request.app.state.session_factory)` | `stores.users` |
| `WorkspaceStore(request.app.state.session_factory)` | `stores.workspaces` |
| `RemoteEvaluatorStore(request.app.state.session_factory)` | `stores.evaluators` |
| `workspace_ref(session, name)` | `await workspace_ref(stores, name)` |
| `dataset_by_ref(session, ref)` / `get_dataset(session, ws, name)` / `list_datasets(session, ws)` / `find_dataset_row(session, ws, name)` | the same with `stores.tenant_id` as the second argument |
| `agent_definition(session, ref)` / `agent_id_for(session, ref)` | the same with `stores.tenant_id` as the second argument |
| `known_blobs(session, shas)` / `blob_size(session, sha)` / `record_blob(session, sha, size, by)` | the same with `stores.tenant_id` as the second argument |
| `_store(request)` helpers in `evaluators.py`, `llm_providers.py`, `trace_sources.py`, `users.py`, `workspaces.py` | delete the helper; routes take `stores: StoresDep` |

File by file:

- `agents.py`: `create_agent` calls `ws = await writable_endpoint(stores, actor, cfg, body.workspace, api_key=..., url=...)` and `stores.agents.create(ws, body.name, ...)`; `edit_agent`, `archive_agent`, `restore_agent` call `await writable(stores, actor, current.workspace)` and use `stores.agents`. `list_agents`, `get_agent` use `stores.agents`.
- `datasets.py`: `list_all` uses `list_datasets(session, stores.tenant_id, workspace)`; `create` uses `workspace_ref(stores, body.workspace)`; every `dataset_by_ref(session, ref)` passes `stores.tenant_id`; `_with_review` takes `stores` instead of `session` for the `workspace_ref` call and `stores.review` instead of `review_log`.
- `evaluators.py`: as `agents.py`, with `stores.evaluators.create(ws, body.name, body.kind, settings, sealed)`.
- `experiments.py`: `create_experiment` reads `agent = await stores.agents.get(...)`, `agent_ws = await workspace_ref(stores, agent.workspace)`, `dataset_ws = await workspace_ref(stores, (await dataset_by_ref(session, stores.tenant_id, body.dataset)).workspace)`, `providers = stores.providers`, `run_deps = stores.deps` and `_scoring(request, with_keys(stores.deps, keys), ...)`, `prepare_experiment(stores.store, ...)`, `remote_evaluators=await stores.evaluators.in_workspace(...)`, `_last_git_revision(session, stores.tenant_id, body.dataset)`. `may_change(stores, session, actor, exp)` calls `workspace_ref(stores, exp.workspace)`; `_creator_id` adds `User.tenant_id == stores.tenant_id`; `_changeable(stores, session, actor, exp_id)`; `_runner_written(session, stores.tenant_id, exp)` uses `agent_definition(session, stores.tenant_id, ...)`; every `deps.` becomes `stores.deps.`, every `store.` becomes `stores.store.`; `list_experiments` with `workspace_id = (await workspace_ref(stores, workspace)).id`; the blob routes use `stores.deps.blob_store`, which is now the tenant-prefixed store, so `trajectory_key(exp.agent_id, ...)` keeps producing the logical key the client sent.
- `server_keys.py`: `server_keys(stores: TenantStores, cfg: Config, agent, judge_base_url)` reads `loader = stores.keys` and `stores.evaluators`; its callers (`experiments.py`, `harness_check.py`) pass `stores` and `request.app.state.cfg`.
- `fixtures.py`: `list_fixtures` uses `workspace_ref(stores, workspace or actor.username)`; `_known(session, tenant_id, sha256)`; `head`, `download`, `upload` take `stores: StoresDep` and use `stores.deps.blob_store` and `stores.tenant_id`.
- `harness_check.py`: `stores.agents`, `workspace_ref(stores, ...)`, `server_keys(stores, cfg, definition, None)`, `stores.deps.blob_store`, `sessions=stores.sessions`.
- `imports.py`: `lint_endpoint` uses `stores.store`, `workspace_ref(stores, ref.workspace)`, `stores.evaluators.names()`, `_agent_input_schema(stores.deps, agent)`; `sync` uses `workspace_ref(stores, target)`, `stores.workspaces.get_by_id(...)`, `import_dataset(stores.store, ...)`, `stores.evaluators.names()`.
- `llm_providers.py`, `trace_sources.py`: `stores.providers` / `stores.sources`; `keys.seal(...)` becomes `stores.keys.seal(...)`.
- `meta.py`: `stores.agents.list_agents()` and `stores.sources.list_sources()`.
- `otel.py`: `export_traces` builds `stores = build_tenant_stores(request.app.state, principal.tenant_id)` first (the collector has no `CurrentUser`, so it cannot use `StoresDep`), opens `async with stores.session() as session:` for `load_actor`, and passes `stores` to `_resolve`/`_user_record`, which call `workspace_ref(stores, ...)` and use `stores.agents`, `stores.spans`, `stores.mappings`. Remove `SessionDep`, `AgentStoreDep`, `SpanStoreDep`, `MappingStoreDep` from its signature.
- `pull.py`, `runs.py`, `traces.py`, `tokens.py`, `trace_imports.py`: substitute per the table; `_launch` in `trace_imports.py` passes `stores.imports` and `stores.spans`; `start_import` uses `stores.sources.resolve(body.source)`, `stores.imports.create(...)`.
- `review.py`: `_dataset_workspace(stores, session, ref)`; `_not_target(stores, ws, slug, case_id)` uses `stores.workspaces.get_by_id`; `stores.workspaces.members(...)`; `review_log` → `stores.review`.
- `users.py`: `stores.users`.
- `workspaces.py`: `stores.workspaces`; `_view` adds `"tenant_id": ws.tenant_id` (Task 12 covers the client side).

MCP: in `src/evalkit/api/mcp/tools.py`, add

```python
from fastmcp.server.dependencies import get_access_token

from evalkit.api.access import TenantStores, build_tenant_stores


def _stores(state: State) -> TenantStores:
    token = get_access_token()
    assert token is not None
    return build_tenant_stores(state, int(token.claims["tenant_id"]))
```

and substitute: `self.state.agents` → `_stores(self.state).agents`; `self.state.session_factory()` → `_stores(self.state).session()`; `self.state.runner_deps` → `_stores(self.state).deps`; `self.state.sessions` → `_stores(self.state).sessions`; `self.state.spans` → `_stores(self.state).spans`; and inside `register`, `state.agents` / `state.session_factory()` / `state.store` / `state.runner_deps` likewise through `_stores(state)`. Build `stores = _stores(...)` once at the top of each method or tool and reuse it. `Runs.__init__` keeps `state` and its semaphore.

`tests/native.py` `serve` drops `agents=env.deps.agents` from its `create_app` call, as do `tests/cli/test_cli.py`, `tests/sdk/test_run_experiment.py`, `tests/db/test_trace_sources.py`, `tests/api/test_mcp.py`, `tests/api/test_cli_dist.py`, `tests/api/test_review_fixes.py`, `tests/flows/helpers_01_06.py`, `tests/flows/helpers_12_17.py` if they pass it. `tests/api/test_traces.py:76-108`, `tests/flows/test_flow_14_import_from_trace_source.py:37` and `tests/flows/test_flow_17_agent_and_evaluator_admin.py:204` set `app.state.trace_sources` to a store with or without a box: set `app.state.box` instead (`None` or the box) and read `build_tenant_stores(app.state, 1).sessions` / `.spans` where they read `app.state.sessions` / `.spans`. `tests/api/test_mcp.py:668` patches `client.app.state.agents.list_agents`: patch `AgentStore.list_agents` on the class instead (`monkeypatch.setattr(AgentStore, "list_agents", broken)`).

- [ ] **Step 6: Run the API, flow, CLI and SDK tests**

Run: `uv run pytest tests/api tests/flows tests/cli tests/sdk tests/core tests/client tests/db -n auto`
Expected: PASS. Work through failures file by file; each is a missed substitution from the table. `uv run ruff check src tests` must be clean (unused imports of the removed `*Dep` names are the usual leftover).

- [ ] **Step 7: Commit**

```bash
git add src/evalkit/api tests/api tests/native.py tests/cli tests/sdk tests/db tests/flows
git commit -m "Build every store per request from the actor's tenant; routes read stores off TenantStores

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

(`git add src/evalkit/api` and the test directories stage only tracked-or-new files under those paths; check `git status` first that nothing unrelated sits there.)

---

### Task 11: Cross-tenant API tests and the opener-set test

**Files:**
- Modify: `tests/db/test_tenancy.py`
- Create: `tests/api/test_cross_tenant.py`

**Interfaces:**
- Consumes: everything above.
- Produces: the two cross-tenant tests the spec names and the test that pins which functions open `ALL_TENANTS` sessions.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_cross_tenant.py`:

```python
import httpx

from evalkit.api.access import build_tenant_stores
from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.models import Tenant
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.identity import seed_agent
from tests.native import api_env, start
from evalkit.core.runner import ExperimentRequest


async def _second_tenant(client, raw_session_factory) -> httpx.AsyncClient:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    root = await UserStore(raw_session_factory, 2).create("root", "password", is_admin=True)
    await WorkspaceStore(raw_session_factory, 2).create_shared("canonical", root.id)
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    resp = await other.post("/api/auth/login", json={"username": "root", "password": "password"})
    assert resp.status_code == 200, resp.text
    other.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return other


async def test_an_experiment_by_id_is_404_from_another_tenant(tmp_path, session_factory, raw_session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
        assert (await client.get(f"/api/experiments/{exp.id}")).status_code == 200
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.get(f"/api/experiments/{exp.id}")
        assert resp.status_code == 404
        resp = await other.get(f"/api/experiments/{exp.id}/runs")
        assert resp.status_code == 404


async def test_a_workspace_by_name_resolves_inside_the_callers_tenant(tmp_path, session_factory, raw_session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        ours = (await client.get("/api/workspaces/canonical")).json()
        theirs = (await other.get("/api/workspaces/canonical")).json()
        assert ours["id"] != theirs["id"]
        assert (ours["tenant_id"], theirs["tenant_id"]) == (1, 2)
        assert (await other.get("/api/datasets/finance@canonical")).status_code == 404
        assert (await other.get("/api/workspaces/akshat")).status_code == 404


async def test_a_member_grant_cannot_cross_tenants(tmp_path, session_factory, raw_session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.post(
            "/api/workspaces/canonical/members",
            json={"username": "alice", "role": WorkspaceRole.WRITER.value},
        )
        assert resp.status_code == 404
```

Match `start(...)`'s `ExperimentRequest` to the shape the existing `tests/api/test_api.py` uses (copy one call). The datasets route path is whatever `tests/api/test_api.py` uses for a dataset by ref.

Append to `tests/db/test_tenancy.py`:

```python
import ast
from pathlib import Path

import evalkit

EXPECTED_OPENERS = {
    ("evalkit.db.auth", "find_token"),
    ("evalkit.db.experiments", "reap"),
    ("evalkit.db.experiments", "tenant_of_experiment"),
    ("evalkit.db.trace_imports", "reap_imports"),
    ("evalkit.db.trace_imports", "tenant_of_import"),
    ("evalkit.db.users", "any_admin_exists"),
    ("evalkit.db.rekey", "count_sealed"),
    ("evalkit.db.rekey", "rekey_all"),
    ("evalkit.db.session", "scope_to_tenant"),
}


def _openers() -> set[tuple[str, str]]:
    root = Path(evalkit.__file__).parent
    found = set()
    for path in root.rglob("*.py"):
        module = "evalkit." + ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                if "ALL_TENANTS" in names:
                    found.add((module, node.name))
    return found


def test_only_the_named_functions_open_all_tenants_sessions():
    assert _openers() == EXPECTED_OPENERS
```

`scope_to_tenant` compares against the sentinel rather than opening a session; it is in the set because the grep-by-name cannot tell the two apart, and that is fine: the point is that no new function appears without a reviewer seeing this set change.

- [ ] **Step 2: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_cross_tenant.py tests/db/test_tenancy.py -v`
Expected: PASS. If `_openers()` reports an extra function, either it is a store that forgot to go through `tenant_session(sf, self._tenant_id)` (fix the store) or a legitimately installation-wide path this plan missed (add it to the set and to the spec's "What stays installation-wide" list in the same commit).

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_cross_tenant.py tests/db/test_tenancy.py
git commit -m "Test that ids and names never cross tenants and pin the ALL_TENANTS openers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Workspace payload, client ref, changelog, deployment skill, full verification

**Files:**
- Modify: `src/evalkit/client/workspaces.py` or wherever the client builds a `WorkspaceRef` from the API (`grep -rn "WorkspaceRef(" src/evalkit/client src/evalkit/cli src/evalkit/sdk` finds it; today none exists, so this step may be a no-op)
- Modify: `CHANGELOG.md`
- Modify: `skills/evalkit-deployment/references/config-and-deploy.md` ("Upgrades" section)
- Modify: `contracts/` only if `scripts/gen_schemas.py` changes anything

**Interfaces:**
- Consumes: `GET /api/workspaces/{ws}` with `tenant_id` (Task 10).
- Produces: a changelog entry; the upgrade step in the deployment skill; a green full suite, ruff, mypy.

- [ ] **Step 1: Client side of `tenant_id`**

Run `grep -rn "WorkspaceRef(" src/evalkit/client src/evalkit/cli src/evalkit/sdk`. If nothing is found, the client never constructs a `WorkspaceRef` and nothing changes. If a site is found, pass `tenant_id=payload["tenant_id"]` there.

- [ ] **Step 2: Changelog**

Under `## Unreleased` in `CHANGELOG.md`, add:

```markdown
- The database gains a `tenants` table with one row, `default`, and a
  `tenant_id` on `users`, `workspaces`, `cases`, `llm_providers`,
  `trace_sources` and `fixture_blobs`. Usernames, workspace names, case ids,
  provider names and trace source names are now unique per tenant. The
  default judge moved from `server_settings` onto the tenant row. Migration
  `0016`; no behaviour change for a single installation.
- Blob objects now live under `tenants/<id>/`. After `evalkit db upgrade`,
  run `evalkit blobs relocate --config <config>` once to move existing
  trajectories, artifacts, run workspaces and fixtures; the server refuses to
  start while any object remains under the old layout. The upgrade has
  downtime between the migration and the relocation.
- `GET /api/workspaces/...` payloads include `tenant_id`.
```

- [ ] **Step 3: Deployment skill**

In `skills/evalkit-deployment/references/config-and-deploy.md`, under `## Upgrades`, after the `evalkit db upgrade` step, add:

```markdown
When upgrading to the release that added tenants (migration `0016`), run the
blob relocation once after the migration and before starting the new server:

    docker compose run --rm --no-deps server evalkit blobs relocate --config /etc/evalkit/config.yaml

It moves every object under `tenants/1/` and is safe to re-run. The new
server refuses to start until it has run, with a message naming the command.
Expect downtime between the migration and the relocation.
```

- [ ] **Step 4: Contracts**

Run: `uv run python scripts/gen_schemas.py && git status --short contracts/`
Expected: no changes; the contracts cover wire models, not the workspace payload. If a file changed, stage it.

- [ ] **Step 5: Full verification**

Run:

```bash
uv run ruff check && uv run ruff format --check && uv run mypy src/evalkit && uv run pytest -n auto
```

Expected: all four clean. `uv run ruff format` fixes formatting if the check fails. For mypy, the usual offenders are the store constructors' new positional argument in `tests/` (not type-checked) and `replace(template, ...)` in `build_tenant_stores`, which is typed through `RunnerDeps`'s dataclass fields.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md skills/evalkit-deployment/references/config-and-deploy.md contracts
git commit -m "Changelog and upgrade notes for tenants and blob relocation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Then open a draft PR against `develop` titled "Tenants: tenant_id on root tables, scoped stores, session guard, blobs under tenants/ (plan 1 of 3)" with the repo's PR template (TL;DR / How / Testing / Checklist); CI runs only once it is marked ready.
