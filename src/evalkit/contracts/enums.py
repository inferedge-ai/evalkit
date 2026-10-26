from enum import StrEnum


class WorkspaceRole(StrEnum):
    WRITER = "writer"
    REVIEWER = "reviewer"


class TokenKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    TICKET = "ticket"


class TrajectorySource(StrEnum):
    ATIF = "atif"
    OTEL = "otel"


class EvaluatorKind(StrEnum):
    SCORE = "score"
    GAUGE = "gauge"


class EvaluationSource(StrEnum):
    BUILTIN = "builtin"
    REMOTE = "remote"
    CODE = "code"


class MappingStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class TraceProvider(StrEnum):
    PHOENIX = "phoenix"


class TraceImportStatus(StrEnum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DONE = "done"
    FAILED = "failed"


class DatasetKind(StrEnum):
    SUITE = "suite"


class SyncSource(StrEnum):
    GIT = "git"
    PROMOTION = "promotion"


class ExperimentStatus(StrEnum):
    RUNNING = "running"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class TrajectoryStatus(StrEnum):
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class Bucket(StrEnum):
    CONTENT = "content"
    TRAJECTORY = "trajectory"
    PRESENTATION = "presentation"
    EFFICIENCY = "efficiency"
    CUSTOM = "custom"
