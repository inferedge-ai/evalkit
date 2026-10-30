// ── Auth ─────────────────────────────────────────────────────────────────
export interface Me {
  username: string;
  is_admin: boolean;
  personal_workspace: string | null;
  // workspace name → the roles this user holds there (writer, reviewer).
  roles: Record<string, WorkspaceRole[]>;
}

export type WorkspaceRole = "writer" | "reviewer";

// ── Workspaces (mirrors api/workspaces.py) ───────────────────────────────
export interface WorkspaceInfo {
  id: number;
  name: string;
  type: "personal" | "shared";
  archived: boolean;
  promotes_to: string | null;
  promoted_from: string | null;
}

export interface MemberRow {
  username: string;
  role: WorkspaceRole;
  granted_by: string;
  created_at: string;
}

export interface UserRow {
  id: number;
  username: string;
  is_admin: boolean;
  archived: boolean;
  created_at: string;
}

// ── Settings (mirrors contracts/settings.py) ─────────────────────────────
export interface JudgeSettings {
  base_url: string;
  model: string;
  reasoning_effort: string;
  max_tokens: number;
  concurrency: number;
}

export interface AgentSettings {
  max_concurrent_runs: number;
  trajectory_wait_seconds: number;
  run_timeout_seconds: number;
  poll_interval_seconds: number;
  stop_grace_seconds: number;
  provides: Record<string, boolean> | null;
}

export interface RemoteEvaluatorInfo {
  name: string;
  workspace: string;
  ref: string;
  kind: "score" | "gauge";
  settings: { endpoint: string; timeout_seconds: number };
  archived: boolean;
  has_api_key: boolean;
}

export interface LlmProviderInfo {
  name: string;
  base_url: string;
  has_api_key: boolean;
  archived: boolean;
}

export interface TraceSourceInfo {
  id: number;
  name: string;
  provider: "phoenix";
  url: string;
  archived: boolean;
}

// ── Datasets (mirrors store/base.py DatasetRow, CaseView) ──────────────
export interface DatasetSync {
  id: number;
  source: "git" | "promotion";
  detail: {
    sha?: string;
    branch?: string;
    dirty?: boolean;
    actor?: string;
    row_count?: number;
    counts?: Record<string, number>;
  } & Record<string, unknown>;
  actor: string;
  created_at: string;
}

export interface Dataset {
  id: number;
  workspace: string;
  workspace_is_shared: boolean;
  name: string;
  kind: "suite";
  last_sync: DatasetSync | null;
  // name@workspace, the reference every API path takes.
  ref: string;
}

export interface CaseFile {
  key: string;
  sha256: string;
  context: string;
}

export interface CaseView {
  case_ref: number;
  case_id: string;
  content_hash: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown> | null;
  evaluators: Record<string, unknown>;
  effective_evaluators: Record<string, unknown>;
  tags: string[];
  split_tags: string[];
  source: string | null;
  files: CaseFile[];
  review_state: string | null;
  review?: ReviewSummary | null;
  twin?: TwinStatus | null;
}

export interface TwinStatus {
  workspace: string;
  state: "absent" | "same" | "differs";
  content_hash: string | null;
  review: ReviewSummary | null;
  overlays_differ: boolean;
  href: string;
}

export interface SplitInfo {
  name: string;
  count: number;
}

// ── Experiments (mirrors store/base.py Experiment) ───────────────────────
export type ExperimentState = "running" | "evaluating" | "completed" | "failed";

export interface GitRevision {
  sha?: string | null;
  branch?: string | null;
  dirty?: boolean | null;
}

export interface ExperimentConfig {
  model: string | null;
  judge: JudgeSettings;
  selection: { all?: boolean; tag?: string | null; case_ids?: string[] | null };
  revision: GitRevision | null;
  snapshot: {
    capabilities: Record<string, unknown>;
    trajectory_source: "atif" | "otel";
    trajectory_wait_seconds: number;
    remote_evaluators: Record<
      string,
      { endpoint: string; timeout_seconds: number }
    >;
    code_evaluators: string[];
    evalkit_version: string | null;
  };
}

export interface Progress {
  total: number;
  ran: number;
  evaluated: number;
}

export interface Experiment {
  id: number;
  number: number;
  workspace: string;
  dataset: string;
  agent: string;
  agent_id: number;
  created_by: string;
  status: ExperimentState;
  status_error: string | null;
  lease_holder: string | null;
  lease_expires_at: string | null;
  repetitions: number;
  config: ExperimentConfig;
  started_at: string;
  ended_at: string | null;
}

export interface ExperimentListItem extends Experiment {
  progress: Progress;
  // Bucket name → mean, by the scorecard's bucket rules.
  scores: Record<string, number | null>;
  cost_usd: number | null;
  avg_latency_ms: number | null;
}

export type CaseRunState = "pending" | "ran" | "evaluated";

export interface ExperimentInfo extends Experiment {
  progress: Progress;
  case_states: Record<string, CaseRunState>;
  can_change: boolean;
  server_may_drive: boolean;
  resumable: boolean;
  pending_runs: boolean;
}

// One experiment's bucket scores for a single case (score-history views).
export interface CaseScorePoint {
  experiment_id: number;
  number: number;
  agent: string;
  model: string | null;
  created_by: string;
  created_at: string;
  repetitions: number;
  // Keyed by bucket name (content, trajectory, presentation, efficiency,
  // custom); see bucketColumns.ts for which are shown.
  scores: Record<string, number | null>;
  cost_usd: number | null;
}

export interface CaseScoresResponse {
  case_id: string;
  points: CaseScorePoint[];
}

// ── Server meta (/api/meta) ──────────────────────────────────────────────
export interface Meta {
  // A CSS colour from server.accent_color, or null for the default blue.
  accent_color: string | null;
  // Shared agents, as full refs (name@workspace).
  agents: string[];
  // agent ref (name@workspace) → repo base URL, e.g. https://github.com/inferedge-ai/evalkit
  agent_repos: Record<string, string>;
  // otel.settle_seconds: how long a session must be quiet before it is settled.
  settle_seconds?: number;
  // Names of the trace sources configured for history import.
  trace_sources?: string[];
}

// ── Scorecard (mirrors core/scorecard.py Report) ─────────────────────────
export interface CaseRow {
  case_id: string;
  scores: Record<string, number | null>;
  explanations: Record<string, string | null>;
  gauges: Record<string, number | null>;
  errors: Record<string, string>;
  run_errors: string[];
  repetitions: number;
  overall: number | null;
  baseline_overall: number | null;
  delta: number | null;
}

export interface EvaluatorStat {
  name: string;
  score: number | null;
  cases: number;
  errors: number;
  bucket: string | null;
  source: string;
}

export interface GaugeStat {
  name: string;
  mean: number | null;
  median: number | null;
  count: number;
}

export interface Report {
  experiment_id: string;
  name: string;
  dataset: string;
  metadata: Record<string, unknown>;
  cases: CaseRow[];
  evaluators: EvaluatorStat[];
  buckets: Record<string, number | null>;
  gauges: GaugeStat[];
  evaluator_columns: string[];
  baseline_id: string | null;
}

// ── Evaluation rows on a run (mirrors store/base.py EvaluationRow) ──────
export interface EvaluationRow {
  producer: string;
  name: string;
  kind: "score" | "gauge";
  source: "builtin" | "remote" | "code";
  value: number | null;
  label: string | null;
  explanation: string | null;
  error: string | null;
}

// The card renderer's shape (detail parsed out of the explanation JSON).
export interface EvalScore {
  score: number | null;
  label: string | null;
  explanation: string | null;
  detail?: Record<string, unknown>;
  items?: Record<string, unknown>[];
  summary?: Record<string, number>;
}

// ── Agents / Harness API proxies ─────────────────────────────────────────
export interface AgentInfo {
  id: number;
  name: string;
  workspace: string;
  // name@workspace, the reference every API path takes.
  ref: string;
  // Same as ref: slugs are always name@workspace.
  slug: string;
  workspace_type: "personal" | "shared";
  harness_url: string | null;
  // "otel" when trajectories come from collected spans, else "atif".
  trajectory_source: "atif" | "otel";
  repo_url: string | null;
  settings: AgentSettings;
  // Who drives the agent: the server, its owner's CLI, or the SDK.
  driver: "server" | "cli" | "sdk";
  archived: boolean;
  // A key is stored on the server (shared agents only); its value is never returned.
  has_api_key: boolean;
}

// ── Traces (mirrors db/mappings.py and core/traces/sessions.py) ──────────
export interface MappingRecord {
  id: number;
  version: number;
  status: "draft" | "active" | "retired";
  document: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  activated_at: string | null;
}

export interface EsfMessage {
  role: string;
  text?: string | null;
  reasoning?: string | null;
  tool_calls?: { id: string; name: string }[] | null;
  tool_call_id?: string | null;
}

export interface EsfSpan {
  span_id: string;
  trace_id: string;
  parent_id: string | null;
  name: string;
  kind: "llm" | "tool" | "agent" | "chain" | "retriever" | "other";
  started_at: string;
  ended_at: string | null;
  error: string | null;
  model?: string | null;
  input_messages?: EsfMessage[] | null;
  output_messages?: EsfMessage[] | null;
  tool_name?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cached_tokens?: number | null;
  cache_write_tokens?: number | null;
  unmapped: string[];
}

export interface SessionTrajectory {
  settled: boolean;
  root_present: boolean;
  span_count: number;
  mapping_version: number | null;
  unmapped_attributes: string[];
  trajectory: AtifTrajectory | null;
  esf?: EsfSpan[] | null;
}

export interface RunListItem {
  run_id: string;
  title: string | null;
  user_id: string | null;
  client_id?: string | null;
  created_at: string | null;
  status?: string | null;
  experiment_id?: number | null;
  experiment_number?: number | null;
  case_id?: string | null;
  stored?: boolean;
}

export interface RunList {
  runs: RunListItem[];
  total: number;
  harness_note?: string;
}

export interface FileEntry {
  path: string;
  size: number;
  content_type: string | null;
}

export interface HarnessCheckReport {
  agent: string;
  checks: Record<
    string,
    { status: "pass" | "warn" | "fail" | "skipped"; detail: string }
  >;
}

// ── ATIF trajectory (mirrors contracts/atif.py) ──────────────────────────
export interface AtifContentPart {
  type: string;
  text?: string;
  media_type?: string;
  path?: string;
}

export interface AtifToolCall {
  tool_call_id: string;
  function_name: string;
  arguments: Record<string, unknown>;
  extra?: Record<string, unknown> | null;
}

export interface AtifSubagentRef {
  trajectory_id?: string | null;
  trajectory_path?: string | null;
  session_id?: string | null;
}

export interface AtifObservationResult {
  source_call_id?: string | null;
  content?: string | AtifContentPart[] | null;
  subagent_trajectory_ref?: AtifSubagentRef[] | null;
  extra?: Record<string, unknown> | null;
}

export interface AtifStepMetrics {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cached_tokens?: number | null;
  cost_usd?: number | null;
  extra?: Record<string, unknown> | null;
}

export interface AtifStep {
  step_id: number;
  timestamp?: string | null;
  source: "user" | "agent" | "system";
  message?: string | AtifContentPart[] | null;
  reasoning_content?: string | null;
  tool_calls?: AtifToolCall[] | null;
  observation?: { results: AtifObservationResult[] } | null;
  metrics?: AtifStepMetrics | null;
  model_name?: string | null;
}

export interface AtifTrajectory {
  schema_version: string;
  session_id?: string | null;
  trajectory_id?: string | null;
  agent?: {
    name: string;
    version?: string | null;
    model_name?: string | null;
  } | null;
  steps: AtifStep[];
  final_metrics?: {
    total_prompt_tokens?: number | null;
    total_completion_tokens?: number | null;
    total_cached_tokens?: number | null;
    total_cost_usd?: number | null;
    total_steps?: number | null;
  } | null;
  subagent_trajectories?: AtifTrajectory[] | null;
  extra?: Record<string, unknown> | null;
}

// ── Run detail (mirrors store/base.py RunDetail) ─────────────────────────
export interface AgentRunRecord {
  harness_run_id: string;
  status: string;
  answer: string | null;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  trajectory_key: string | null;
  trajectory_status: string | null;
  metrics: Record<string, unknown>;
  title: string | null;
}

export interface RunDetail {
  experiment_run_id: number;
  case_id: string;
  repetition: number;
  agent_run_id: number;
  agent_run: AgentRunRecord;
  evaluations: EvaluationRow[];
}

// ── Imports ──────────────────────────────────────────────────────────────
export interface LintFinding {
  severity: string;
  code: string;
  case_id: string;
  message: string;
}

// ── Review ───────────────────────────────────────────────────────────────
export type ReviewState =
  "unreviewed" | "review_requested" | "changes_requested" | "approved";

export interface ReviewSummary {
  state: ReviewState;
  requested_to: string | null;
  updated_at: string | null;
}

export interface CaseEvent {
  id: number;
  dataset: string;
  case_id: string;
  content_hash: string;
  kind: "synced" | "review_requested" | "approved" | "changes_requested";
  actor: string;
  target: string | null;
  created_at: string;
}

export interface CaseComment {
  id: number;
  dataset: string;
  case_id: string;
  content_hash: string;
  parent_id: number | null;
  author: string;
  body: string;
  resolved: boolean;
  created_at: string;
}

export interface CommentThread extends CaseComment {
  replies: CaseComment[];
}

export interface QueueItem {
  dataset: string;
  case_id: string;
  requested_to: string | null;
  requested_by: string;
  requested_at: string | null;
  input: string;
}

export type CliInfo = {
  version: string;
  filename: string;
  sha256: string;
  size: number;
};
