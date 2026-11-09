import { useState } from "react";
import type { FormEvent } from "react";
import { ArrowLeft, X } from "lucide-react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { canManageMembers, canWrite, groupMembers } from "../lib/access";
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
import { Tabs, ToArrow } from "../lib/ui";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { BuiltinEvaluatorsNote } from "./BuiltinEvaluatorsNote";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

type WorkspaceTab = "evaluators" | "members";
const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: "evaluators", label: "Evaluators" },
  { id: "members", label: "Members" },
];

function isTab(value: string | undefined): value is WorkspaceTab {
  return TABS.some((t) => t.id === value);
}

function EvaluatorsTab({
  ws,
  writable,
}: {
  ws: WorkspaceInfo;
  writable: boolean;
}) {
  const listed = useApi<RemoteEvaluatorInfo[]>(
    `/evaluators?workspace=${encodeURIComponent(ws.name)}&include_archived=true`,
  );
  const rows = listed.data ?? null;
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

  const canEdit = writable && !ws.archived;
  return (
    <>
      <div className="toolbar mb-2">
        <p className="row-meta whitespace-normal">
          Runs of this workspace's agents are scored with these evaluators.{" "}
          {ws.type === "shared"
            ? "Their API keys are stored encrypted on the server and never shown again."
            : PERSONAL_KEY_NOTE}
        </p>
        {canEdit && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => setEditing("new")}
          >
            New evaluator
          </Button>
        )}
      </div>
      <BuiltinEvaluatorsNote />
      {error && <div className="pane-status error">{error}</div>}
      <Card className="rows gap-0 py-0">
        {rows?.map((row) => (
          <div
            key={row.ref}
            className="row grid-cols-[minmax(0,1fr)_auto_auto_minmax(0,2fr)_auto_auto]"
          >
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
        {rows && !rows.length && (
          <div className="pane-status">No remote evaluators.</div>
        )}
      </Card>
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
  const rows = listed.data ?? null;
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
      <div className="pane-status">
        A personal workspace has no members: only @{ws.name} writes to it.
      </div>
    );
  return (
    <>
      {manage && (
        <form
          className="toolbar mb-2"
          onSubmit={(e) => {
            e.preventDefault();
            run(() =>
              apiJson(base, "POST", { username: username.trim(), role }),
            );
            setUsername("");
          }}
        >
          <Input
            className="w-auto"
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
      <p className="row-meta mb-2">
        Writers sync datasets and edit agents and evaluators. Reviewers approve
        cases or request changes. Both can run experiments, and one member can
        hold both roles.
      </p>
      {error && <div className="pane-status error">{error}</div>}
      <Card className="rows gap-0 py-0">
        {rows &&
          groupMembers(rows).map((member) => (
            <div
              key={member.username}
              className="row grid-cols-[minmax(0,1fr)_auto]"
            >
              <span className="row-strong">{member.username}</span>
              <span className="flex items-center justify-end gap-3">
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
        {rows && !rows.length && <div className="pane-status">No members.</div>}
      </Card>
    </>
  );
}

export function WorkspacePage() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const me = useMe();
  const { workspace: ws, workspaces, select } = useWorkspace();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!isTab(tab)) return <Navigate to="/workspace/evaluators" replace />;
  const writable = canWrite(me, ws);
  const mayArchive =
    ws.type === "shared" ? me.is_admin : ws.name === me.personal_workspace;
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
  const create = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const created = await apiJson<WorkspaceInfo>("/workspaces", "POST", {
        name: name.trim(),
      });
      setName("");
      await refreshQueries();
      select(created.name);
    });
  };
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
        breadcrumbs={[{ label: "Settings", to: "/workspace" }]}
        title={`@${ws.name}`}
        actions={
          mayArchive && (
            <Button variant="outline" size="sm" onClick={toggleArchive}>
              {ws.archived ? "Restore" : "Archive"}
            </Button>
          )
        }
      />
      <div className="page">
        <div className="toolbar mb-2">
          <Badge variant="secondary">{ws.type}</Badge>
          {ws.archived && <Badge variant="secondary">archived</Badge>}
          {ws.promotes_to && (
            <Badge variant="info" title="promotion target">
              <ToArrow /> {ws.promotes_to}
            </Badge>
          )}
          {ws.promoted_from && (
            <Badge variant="info" title="promotion source">
              <ArrowLeft
                size={12}
                role="img"
                aria-label="from"
                className="inline shrink-0 align-[-0.125em]"
              />{" "}
              {ws.promoted_from}
            </Badge>
          )}
          {me.is_admin && ws.type === "shared" && !ws.promoted_from && (
            <label className="row-meta flex items-center gap-1">
              promotes to
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
        </div>
        {me.is_admin && (
          <form className="toolbar mb-3" onSubmit={create}>
            <Input
              className="w-auto"
              placeholder="shared workspace name, e.g. dev"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <Button type="submit">Create workspace</Button>
          </form>
        )}
        {error && <div className="pane-status error">{error}</div>}
        <Tabs<WorkspaceTab>
          tabs={TABS}
          active={tab}
          onSelect={(next) => navigate(`/workspace/${next}`)}
        />
        <div className="mt-4">
          {tab === "evaluators" && (
            <EvaluatorsTab ws={ws} writable={writable} />
          )}
          {tab === "members" && <MembersTab ws={ws} />}
        </div>
      </div>
    </>
  );
}
