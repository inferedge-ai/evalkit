from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    case,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import ColumnElement

from evalkit.contracts.enums import (
    Bucket,
    DatasetKind,
    EvaluationSource,
    EvaluatorKind,
    ExperimentStatus,
    MappingStatus,
    RunStatus,
    SyncSource,
    TokenKind,
    TraceImportStatus,
    TraceProvider,
    TrajectorySource,
    WorkspaceRole,
)
from evalkit.core.review import CaseEventKind


class Base(DeclarativeBase):
    pass


def check_in(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{v.value}'" for v in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(AuditMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Workspace(AuditMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("name", name="uq_workspaces_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @hybrid_property
    def type(self) -> str:
        return "shared" if self.owner_user_id is None else "personal"

    @type.inplace.expression
    @classmethod
    def _type_expression(cls) -> ColumnElement[str]:
        return case((cls.owner_user_id.is_(None), "shared"), else_="personal")


class WorkspacePromotion(AuditMixin, Base):
    __tablename__ = "workspace_promotions"
    __table_args__ = (UniqueConstraint("target_id", name="uq_workspace_promotions_target"),)

    source_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))


class WorkspaceMember(AuditMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (check_in("role", WorkspaceRole, "ck_workspace_members_role"),)

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    granted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class AuthToken(AuditMixin, Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint("kind <> 'agent' OR agent_id IS NOT NULL", name="ck_auth_tokens_agent"),
        check_in("kind", TokenKind, "ck_auth_tokens_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(8), default=TokenKind.USER, server_default=TokenKind.USER.value, nullable=False
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, default=None
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class Agent(AuditMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_agents_workspace_name"),
        check_in("trajectory_source", TrajectorySource, "ck_agents_trajectory_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(150))
    harness_url: Mapped[str | None] = mapped_column(String(500), default=None)
    trajectory_source: Mapped[str] = mapped_column(
        String(8), default=TrajectorySource.ATIF, server_default=TrajectorySource.ATIF.value
    )
    repo_url: Mapped[str | None] = mapped_column(String(500), default=None)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class RemoteEvaluator(AuditMixin, Base):
    __tablename__ = "remote_evaluators"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_remote_evaluators_workspace_name"),
        check_in("kind", EvaluatorKind, "ck_remote_evaluators_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(8))
    config: Mapped[dict] = mapped_column(JSONB)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TraceSource(AuditMixin, Base):
    __tablename__ = "trace_sources"
    __table_args__ = (
        UniqueConstraint("name", name="uq_trace_sources_name"),
        check_in("provider", TraceProvider, "ck_trace_sources_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    provider: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSONB)
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TraceImport(AuditMixin, Base):
    __tablename__ = "trace_imports"
    __table_args__ = (
        check_in("status", TraceImportStatus, "ck_trace_imports_status"),
        CheckConstraint("since < until", name="ck_trace_imports_range"),
        Index(
            "uq_trace_imports_active",
            "agent_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'stopping')"),
        ),
        Index("ix_trace_imports_active", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    trace_source_id: Mapped[int] = mapped_column(ForeignKey("trace_sources.id"))
    project: Mapped[str] = mapped_column(Text)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resume_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resume_trace_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(String(16))
    traces: Mapped[int] = mapped_column(Integer, default=0)
    spans: Mapped[int] = mapped_column(BigInteger, default=0)
    sample_session: Mapped[str | None] = mapped_column(String(255), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    lease_holder: Mapped[str | None] = mapped_column(String(255), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Span(Base):
    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("agent_id", "trace_id", "span_id", name="uq_spans_agent_trace_span"),
        Index("ix_spans_agent_session", "agent_id", "session_id"),
        Index("ix_spans_agent_trace", "agent_id", "trace_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(String(32))
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_code: Mapped[str] = mapped_column(String(8))
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB)
    events: Mapped[list] = mapped_column(JSONB)
    resource: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False
    )
    external_trace_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("trace_sources.id"), default=None
    )


class SpanMapping(AuditMixin, Base):
    __tablename__ = "span_mappings"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_span_mappings_agent_version"),
        check_in("status", MappingStatus, "ck_span_mappings_status"),
        Index(
            "uq_span_mappings_active",
            "agent_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_span_mappings_draft",
            "agent_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8))
    mapping: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class Case(AuditMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("case_id", name="uq_cases_case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(String(255))


class CaseVersion(AuditMixin, Base):
    __tablename__ = "case_versions"
    __table_args__ = (Index("ix_case_versions_tags", "tags", postgresql_using="gin"),)

    case_ref: Mapped[int] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(16), primary_key=True)
    input: Mapped[dict] = mapped_column(JSONB)
    expected: Mapped[dict | None] = mapped_column(JSONB, default=None)
    evaluators: Mapped[dict] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)


class FixtureBlob(AuditMixin, Base):
    __tablename__ = "fixture_blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)


class CaseVersionFile(AuditMixin, Base):
    __tablename__ = "case_version_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
    )

    case_ref: Mapped[int] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    sha256: Mapped[str] = mapped_column(ForeignKey("fixture_blobs.sha256"))
    context: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class Dataset(AuditMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_datasets_workspace_name"),
        check_in("kind", DatasetKind, "ck_datasets_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(16))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DatasetSync(AuditMixin, Base):
    __tablename__ = "dataset_syncs"
    __table_args__ = (check_in("source", SyncSource, "ck_dataset_syncs_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(16))
    detail: Mapped[dict] = mapped_column(JSONB)


class DatasetCase(AuditMixin, Base):
    __tablename__ = "dataset_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
    )

    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    case_ref: Mapped[int] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(16))
    path_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    source: Mapped[str | None] = mapped_column(String(500), default=None)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CaseEvent(AuditMixin, Base):
    __tablename__ = "case_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "case_ref"], ["dataset_cases.dataset_id", "dataset_cases.case_ref"]
        ),
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
        check_in("kind", CaseEventKind, "ck_case_events_kind"),
        Index("ix_case_events_dataset_case", "dataset_id", "case_ref", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer)
    case_ref: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    sync_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_syncs.id"), default=None)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)


class CaseComment(AuditMixin, Base):
    __tablename__ = "case_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "case_ref"], ["dataset_cases.dataset_id", "dataset_cases.case_ref"]
        ),
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
        Index("ix_case_comments_dataset_case", "dataset_id", "case_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer)
    case_ref: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_comments.id", ondelete="CASCADE"), default=None
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Overlay(AuditMixin, Base):
    __tablename__ = "overlays"
    __table_args__ = (
        UniqueConstraint("source_dataset_id", "target_case", name="uq_overlays_source_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    target_case: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    agents: Mapped[list[str]] = mapped_column(ARRAY(Text))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    evaluators: Mapped[dict] = mapped_column(JSONB)
    source_file: Mapped[str | None] = mapped_column(String(500), default=None)


class Experiment(AuditMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_experiments_dataset_number"),
        check_in("status", ExperimentStatus, "ck_experiments_status"),
        Index("ix_experiments_active", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16))
    status_error: Mapped[str | None] = mapped_column(Text, default=None)
    lease_holder: Mapped[str | None] = mapped_column(String(255), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    repetitions: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ExperimentCase(AuditMixin, Base):
    __tablename__ = "experiment_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_ref", "content_hash"], ["case_versions.case_ref", "case_versions.content_hash"]
        ),
    )

    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), primary_key=True)
    case_ref: Mapped[int] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(16))
    effective_evaluators: Mapped[dict] = mapped_column(JSONB)


class AgentRun(AuditMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("agent_id", "harness_run_id", name="uq_agent_runs_agent_harness_run"),
        check_in("status", RunStatus, "ck_agent_runs_status"),
        CheckConstraint(
            "trajectory_status IS NULL OR trajectory_status IN "
            "('valid', 'partial', 'invalid', 'unavailable')",
            name="ck_agent_runs_trajectory_status",
        ),
        Index("ix_agent_runs_agent_started", "agent_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    harness_run_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    answer: Mapped[str | None] = mapped_column(Text, default=None)
    output: Mapped[dict | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    trajectory_key: Mapped[str | None] = mapped_column(String(500), default=None)
    trajectory_status: Mapped[str | None] = mapped_column(String(16), default=None)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)


class ExperimentRun(AuditMixin, Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "case_ref", "repetition", name="uq_experiment_runs_case_repetition"
        ),
        UniqueConstraint("agent_run_id", name="uq_experiment_runs_agent_run"),
        ForeignKeyConstraint(
            ["experiment_id", "case_ref"],
            ["experiment_cases.experiment_id", "experiment_cases.case_ref"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(Integer)
    case_ref: Mapped[int] = mapped_column(Integer)
    repetition: Mapped[int] = mapped_column(Integer)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))


class Evaluation(AuditMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("experiment_run_id", "name", name="uq_evaluations_run_name"),
        check_in("kind", EvaluatorKind, "ck_evaluations_kind"),
        check_in("source", EvaluationSource, "ck_evaluations_source"),
        CheckConstraint(
            f"bucket IS NULL OR bucket IN ({', '.join(repr(b.value) for b in Bucket)})",
            name="ck_evaluations_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_run_id: Mapped[int] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    producer: Mapped[str] = mapped_column(String(150))
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(8))
    bucket: Mapped[str | None] = mapped_column(String(32), default=None)
    value: Mapped[float | None] = mapped_column(Float, default=None)
    label: Mapped[str | None] = mapped_column(String(150), default=None)
    explanation: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
