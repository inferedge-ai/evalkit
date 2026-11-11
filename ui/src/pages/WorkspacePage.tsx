import { useState } from "react";
import { Gauge, Users, X } from "lucide-react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import {
  canManageMembers,
  getReadOnlyReason,
  groupMembers,
} from "../lib/access";
import { apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { PERSONAL_KEY_NOTE } from "../lib/endpoint";
import { refreshQueries, useApi } from "../lib/query";
import { EvaluatorForm } from "./EvaluatorForm";
import { Select } from "../lib/Select";
import type {
  MemberRow,
  RemoteEvaluatorInfo,
  WorkspaceInfo,
  WorkspaceRole,
} from "../lib/types";
import { Tabs } from "../lib/ui";
import { describePromotion } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { BuiltinEvaluatorsNote } from "./BuiltinEvaluatorsNote";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

type WorkspaceTab = "evaluators" | "members";
const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: "evaluators", label: "Evaluators" },
  { id: "members", label: "Members" },
];

const EVALUATOR_COLUMNS =
  "grid-cols-[minmax(0,1fr)_auto_auto_minmax(0,2fr)_auto_auto]";
const MEMBER_COLUMNS = "grid-cols-[minmax(0,1fr)_auto]";

function isTab(value: string | undefined): value is WorkspaceTab {
  return TABS.some((t) => t.id === value);
}

function describeWorkspace(ws: WorkspaceInfo): string {
  return [
    ws.type === "shared" ? "Shared workspace" : "Personal workspace",
    ws.archived && "archived",
    describePromotion(ws),
  ]
    .filter(Boolean)
    .join(" · ");
}

function SkeletonRows({ columns }: { columns: string }) {
  return (
    <Card className="rows gap-0 py-0">
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className={`row ${columns}`}>
          <Skeleton className="h-4" style={{ width: `${35 + i * 12}%` }} />
          <Skeleton className="h-4 w-14" />
          <Skeleton className="h-3 w-40 max-w-full" />
        </div>
      ))}
    </Card>
  );
}

function EvaluatorsTab({ ws }: { ws: WorkspaceInfo }) {
  const me = useMe();
  const listed = useApi<RemoteEvaluatorInfo[]>(
    `/evaluators?workspace=${encodeURIComponent(ws.name)}&include_archived=true`,
  );
  const rows = listed.data;
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? listed.error?.message ?? null;
  const [editing, setEditing] = useState<RemoteEvaluatorInfo | "new" | null>(
    null,
  );

  const toggleArchive = async (row: RemoteEvaluatorInfo) => {
    setActionError(null);
    try {
      await apiJson(
        `/evaluators/${encodeURIComponent(row.ref)}/${row.archived ? "restore" : "archive"}`,
        "POST",
      );
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const readOnlyReason = getReadOnlyReason(me, ws);
  const canEdit = readOnlyReason === null;
  return (
    <>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-3xl text-xs text-muted-foreground">
          Runs of this workspace's agents are scored with these evaluators.{" "}
          {ws.type === "shared"
            ? "Their API keys are stored encrypted on the server and never shown again."
            : PERSONAL_KEY_NOTE}
        </p>
        {canEdit && (
          <Button variant="outline" size="sm" onClick={() => setEditing("new")}>
            New evaluator
          </Button>
        )}
      </div>
      <BuiltinEvaluatorsNote />
      {error && <div className="pane-status error">{error}</div>}
      {rows === undefined && !listed.error && (
        <SkeletonRows columns={EVALUATOR_COLUMNS} />
      )}
      {rows?.length === 0 && (
        <Card className="py-8">
          <Empty
            icon={Gauge}
            title={`No remote evaluators in @${ws.name}`}
            description={
              readOnlyReason ?? (
                <>
                  Add one with New evaluator, or run{" "}
                  <span className="font-mono">evalkit evaluators create</span>.
                </>
              )
            }
          />
        </Card>
      )}
      {!!rows?.length && (
        <Card className="rows gap-0 py-0">
          {rows.map((row) => (
            <div key={row.ref} className={`row ${EVALUATOR_COLUMNS}`}>
              <span className="row-strong">{row.name}</span>
              <Badge variant="secondary">{row.kind}</Badge>
              {row.archived || ws.type === "shared" ? (
                <Badge variant="secondary">
                  {row.archived
                    ? "archived"
                    : row.has_api_key
                      ? "key set"
                      : "no key"}
                </Badge>
              ) : (
                <span />
              )}
              <span className="row-meta truncate font-mono">
                {row.settings.endpoint}
              </span>
              {canEdit ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={row.archived}
                    onClick={() => setEditing(row)}
                  >
                    edit
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => toggleArchive(row)}
                  >
                    {row.archived ? "restore" : "archive"}
                  </Button>
                </>
              ) : (
                <>
                  <span />
                  <span />
                </>
              )}
            </div>
          ))}
        </Card>
      )}
      {editing && (
        <EvaluatorForm
          ws={ws}
          evaluator={editing === "new" ? undefined : editing}
          onSaved={() => {
            setEditing(null);
            void refreshQueries();
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function MembersTab({ ws }: { ws: WorkspaceInfo }) {
  const me = useMe();
  const manage = canManageMembers(me, ws);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("reviewer");
  const base = `/workspaces/${encodeURIComponent(ws.name)}/members`;
  const listed = useApi<MemberRow[]>(base);
  const rows = listed.data;
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? listed.error?.message ?? null;

  const run = async (call: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await call();
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  if (ws.type === "personal")
    return (
      <Card className="py-8">
        <Empty
          icon={Users}
          title={`No members in @${ws.name}`}
          description={`A personal workspace has no members: only @${ws.name} writes to it.`}
        />
      </Card>
    );
  return (
    <>
      <p className="mb-3 max-w-3xl text-xs text-muted-foreground">
        Writers sync datasets and edit agents and evaluators. Reviewers approve
        cases or request changes. Both can run experiments, and one member can
        hold both roles.
      </p>
      {manage && (
        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            run(() =>
              apiJson(base, "POST", { username: username.trim(), role }),
            );
            setUsername("");
          }}
        >
          <Input
            className="w-full sm:w-56"
            placeholder="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <Select
            ariaLabel="role"
            value={role}
            onChange={setRole}
            options={[
              { value: "reviewer", label: "reviewer" },
              { value: "writer", label: "writer" },
            ]}
          />
          <Button type="submit">Add member</Button>
        </form>
      )}
      {error && <div className="pane-status error">{error}</div>}
      {rows === undefined && !listed.error && (
        <SkeletonRows columns={MEMBER_COLUMNS} />
      )}
      {rows?.length === 0 && (
        <Card className="py-8">
          <Empty
            icon={Users}
            title={`No members in @${ws.name}`}
            description={
              manage ? (
                <>
                  Add one above, or run{" "}
                  <span className="font-mono">
                    evalkit workspaces members add {ws.name} &lt;username&gt;
                    --role writer
                  </span>
                  .
                </>
              ) : undefined
            }
          />
        </Card>
      )}
      {!!rows?.length && (
        <Card className="rows gap-0 py-0">
          {groupMembers(rows).map((member) => (
            <div key={member.username} className={`row ${MEMBER_COLUMNS}`}>
              <span className="row-strong">{member.username}</span>
              <span className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
                {member.grants.map((grant) => (
                  <span
                    key={grant.role}
                    className="inline-flex items-center gap-1.5"
                  >
                    <Badge variant="secondary">{grant.role}</Badge>
                    <span className="row-meta">
                      by {grant.granted_by} {fmtWhen(grant.created_at) ?? ""}
                    </span>
                    {manage && (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        title={`remove the ${grant.role} role from ${member.username}`}
                        aria-label={`remove ${grant.role}`}
                        onClick={() =>
                          run(() =>
                            apiJson(
                              `${base}/${encodeURIComponent(member.username)}/${grant.role}`,
                              "DELETE",
                            ),
                          )
                        }
                      >
                        <X size={12} />
                      </Button>
                    )}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </Card>
      )}
    </>
  );
}

export function WorkspacePage() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const me = useMe();
  const { workspace: ws, workspaces } = useWorkspace();
  const [error, setError] = useState<string | null>(null);
  const [errorWorkspace, setErrorWorkspace] = useState(ws.name);
  if (errorWorkspace !== ws.name) {
    setErrorWorkspace(ws.name);
    setError(null);
  }

  if (!isTab(tab))
    return <Navigate to="/settings/workspace/evaluators" replace />;
  const mayArchive =
    ws.type === "shared" ? me.is_admin : ws.name === me.personal_workspace;
  const mayEditPromotion =
    me.is_admin && ws.type === "shared" && !ws.promoted_from;
  const run = async (call: () => Promise<unknown>) => {
    setError(null);
    try {
      await call();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const toggleArchive = () =>
    run(async () => {
      await apiJson(
        `/workspaces/${encodeURIComponent(ws.name)}/${ws.archived ? "restore" : "archive"}`,
        "POST",
      );
      await refreshQueries();
    });
  const setPromotesTo = (target: string) =>
    run(async () => {
      await apiJson(`/workspaces/${encodeURIComponent(ws.name)}`, "PATCH", {
        promotes_to: target || null,
      });
      await refreshQueries();
    });
  const targets = workspaces.filter(
    (w) =>
      w.type === "shared" &&
      !w.archived &&
      w.name !== ws.name &&
      !w.promotes_to &&
      (!w.promoted_from || w.promoted_from === ws.name),
  );

  return (
    <>
      <PageHeader
        title={`@${ws.name}`}
        description={describeWorkspace(ws)}
        actions={
          mayArchive && (
            <Button variant="outline" size="sm" onClick={toggleArchive}>
              {ws.archived ? "Restore" : "Archive"}
            </Button>
          )
        }
      />
      <div className="page">
        {error && <div className="pane-status error">{error}</div>}
        {mayEditPromotion && (
          <label className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
            Promotes to
            <Select
              ariaLabel="promotion target"
              value={ws.promotes_to ?? ""}
              onChange={setPromotesTo}
              options={[
                { value: "", label: "none" },
                ...targets.map((w) => ({ value: w.name, label: w.name })),
              ]}
            />
          </label>
        )}
        <Tabs<WorkspaceTab>
          tabs={TABS}
          active={tab}
          onSelect={(next) => navigate(`/settings/workspace/${next}`)}
        />
        <div className="mt-4">
          {tab === "evaluators" && <EvaluatorsTab ws={ws} />}
          {tab === "members" && <MembersTab ws={ws} />}
        </div>
      </div>
    </>
  );
}
