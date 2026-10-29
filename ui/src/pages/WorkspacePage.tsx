import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { canManageMembers, canWrite, groupMembers } from "../lib/access";
import { api, apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { ENDPOINT_NOTE, PERSONAL_KEY_NOTE } from "../lib/endpoint";
import { Select } from "../lib/Select";
import type {
  MemberRow,
  RemoteEvaluatorInfo,
  WorkspaceInfo,
  WorkspaceRole,
} from "../lib/types";
import { Tabs } from "../lib/ui";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";

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
  const [rows, setRows] = useState<RemoteEvaluatorInfo[] | null>(null);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [kind, setKind] = useState<"score" | "gauge">("score");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<RemoteEvaluatorInfo[]>(
      `/evaluators?workspace=${encodeURIComponent(ws.name)}`,
    )
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [ws.name]);
  useEffect(load, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await apiJson("/evaluators", "POST", {
        workspace: ws.name,
        name: name.trim(),
        kind,
        settings: { endpoint: endpoint.trim() },
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setName("");
      setEndpoint("");
      setApiKey("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const archive = async (ref: string) => {
    setError(null);
    try {
      await apiJson(`/evaluators/${encodeURIComponent(ref)}/archive`, "POST");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <p className="row-meta mb-2">
        Runs of this workspace's agents are scored with these evaluators.{" "}
        {ws.type === "shared"
          ? "Their API keys are stored encrypted on the server and never shown again."
          : PERSONAL_KEY_NOTE}
      </p>
      {writable && !ws.archived && (
        <form className="toolbar mb-2" onSubmit={create}>
          <input
            className="input"
            placeholder="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <input
            className="input min-w-0 flex-1"
            placeholder="endpoint URL"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            required
          />
          {ws.type === "shared" && (
            <input
              className="input"
              type="password"
              aria-label="API key"
              placeholder="API key (optional)"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
          <Select
            ariaLabel="kind"
            value={kind}
            onChange={setKind}
            options={[
              { value: "score", label: "score" },
              { value: "gauge", label: "gauge" },
            ]}
          />
          <button className="btn btn-primary">Add evaluator</button>
        </form>
      )}
      {writable && !ws.archived && (
        <p className="row-meta mb-2">{ENDPOINT_NOTE}</p>
      )}
      {error && <div className="pane-status error">{error}</div>}
      <div className="card rows">
        {rows?.map((row) => (
          <div
            key={row.ref}
            className="row grid-cols-[minmax(0,1fr)_auto_auto_minmax(0,2fr)_auto]"
          >
            <span className="row-strong">{row.name}</span>
            <span className="chip muted">{row.kind}</span>
            <span className="chip muted">
              {ws.type === "shared"
                ? row.has_api_key
                  ? "key set"
                  : "no key"
                : ""}
            </span>
            <span className="row-meta truncate font-mono">
              {row.settings.endpoint}
            </span>
            {writable ? (
              <button className="btn btn-sm" onClick={() => archive(row.ref)}>
                archive
              </button>
            ) : (
              <span />
            )}
          </div>
        ))}
        {rows && !rows.length && (
          <div className="pane-status">No remote evaluators.</div>
        )}
      </div>
    </>
  );
}

function MembersTab({ ws }: { ws: WorkspaceInfo }) {
  const me = useMe();
  const manage = canManageMembers(me, ws);
  const [rows, setRows] = useState<MemberRow[] | null>(null);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("reviewer");
  const [error, setError] = useState<string | null>(null);
  const base = `/workspaces/${encodeURIComponent(ws.name)}/members`;
  const load = useCallback(() => {
    api<MemberRow[]>(base)
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [base]);
  useEffect(load, [load]);

  const run = async (call: () => Promise<unknown>) => {
    setError(null);
    try {
      await call();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
          <input
            className="input"
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
          <button className="btn btn-primary">Add member</button>
        </form>
      )}
      <p className="row-meta mb-2">
        Writers sync datasets and edit agents and evaluators. Reviewers approve
        cases or request changes. Both can run experiments, and one member can
        hold both roles.
      </p>
      {error && <div className="pane-status error">{error}</div>}
      <div className="card rows">
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
                    <span className="chip muted">{grant.role}</span>
                    <span className="row-meta">
                      by {grant.granted_by} {fmtWhen(grant.created_at) ?? ""}
                    </span>
                    {manage && (
                      <button
                        className="btn btn-sm btn-quiet"
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
                        ×
                      </button>
                    )}
                  </span>
                ))}
              </span>
            </div>
          ))}
        {rows && !rows.length && <div className="pane-status">No members.</div>}
      </div>
    </>
  );
}

export function WorkspacePage() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const me = useMe();
  const { workspace: ws, workspaces, reload, select } = useWorkspace();
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
      await reload();
    });
  const create = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const created = await apiJson<WorkspaceInfo>("/workspaces", "POST", {
        name: name.trim(),
      });
      setName("");
      await reload();
      select(created.name);
    });
  };
  const setPromotesTo = (target: string) =>
    run(async () => {
      await apiJson(`/workspaces/${encodeURIComponent(ws.name)}`, "PATCH", {
        promotes_to: target || null,
      });
      await reload();
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
    <div className="page">
      <div className="toolbar mb-2">
        <h1 className="page-title">@{ws.name}</h1>
        <span className="chip muted">{ws.type}</span>
        {ws.archived && <span className="chip muted">archived</span>}
        {ws.promotes_to && (
          <span className="chip chip-info" title="promotion target">
            → {ws.promotes_to}
          </span>
        )}
        {ws.promoted_from && (
          <span className="chip chip-info" title="promotion source">
            ← {ws.promoted_from}
          </span>
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
        {mayArchive && (
          <button className="btn btn-sm ml-auto" onClick={toggleArchive}>
            {ws.archived ? "Restore" : "Archive"}
          </button>
        )}
      </div>
      {me.is_admin && (
        <form className="toolbar mb-3" onSubmit={create}>
          <input
            className="input"
            placeholder="shared workspace name, e.g. dev"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button className="btn btn-primary">Create workspace</button>
        </form>
      )}
      {error && <div className="pane-status error">{error}</div>}
      <Tabs<WorkspaceTab>
        tabs={TABS}
        active={tab}
        onSelect={(next) => navigate(`/workspace/${next}`)}
      />
      <div className="mt-4">
        {tab === "evaluators" && <EvaluatorsTab ws={ws} writable={writable} />}
        {tab === "members" && <MembersTab ws={ws} />}
      </div>
    </div>
  );
}
