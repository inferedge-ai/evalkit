import { useId, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { SearchX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { apiJson } from "../lib/api";
import { FORM_DIALOG, FORM_DIALOG_BODY } from "../lib/formDialog";
import { refreshQueries } from "../lib/query";
import type { WorkspaceInfo } from "../lib/types";
import { othersPersonal, workspaceHint } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";

const ROW_COLUMNS =
  "grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,1fr)_auto_6rem_minmax(0,14rem)]";

function describeMembers(ws: WorkspaceInfo): string {
  if (ws.type === "personal") return "";
  return `${ws.members} member${ws.members === 1 ? "" : "s"}`;
}

function describePromotion(ws: WorkspaceInfo): string {
  if (ws.promotes_to) return `promotes to @${ws.promotes_to}`;
  if (ws.promoted_from) return `promoted from @${ws.promoted_from}`;
  return "";
}

function WorkspaceRow({
  ws,
  me,
  onOpen,
}: {
  ws: WorkspaceInfo;
  me: string | null;
  onOpen: (name: string) => void;
}) {
  return (
    <button
      type="button"
      className={`row w-full text-left hover:bg-fill ${ROW_COLUMNS}`}
      onClick={() => onOpen(ws.name)}
    >
      <span className="row-strong truncate font-mono">@{ws.name}</span>
      <Badge variant="secondary">{workspaceHint(ws, me)}</Badge>
      <span className="row-meta num hidden sm:block">
        {describeMembers(ws)}
      </span>
      <span className="row-meta hidden truncate sm:block">
        {describePromotion(ws)}
      </span>
    </button>
  );
}

function NewWorkspaceDialog({ onClose }: { onClose: () => void }) {
  const formId = useId();
  const { select } = useWorkspace();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await apiJson<WorkspaceInfo>("/workspaces", "POST", {
        name: name.trim(),
      });
      await refreshQueries();
      select(created.name);
      navigate("/settings/workspace/members");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={FORM_DIALOG}>
        <form className="contents" onSubmit={create}>
          <DialogHeader>
            <DialogTitle>New workspace</DialogTitle>
            <DialogDescription>
              A shared workspace. You add its members next.
            </DialogDescription>
          </DialogHeader>
          <div className={FORM_DIALOG_BODY}>
            <Field>
              <FieldLabel htmlFor={`${formId}-name`}>Name</FieldLabel>
              <Input
                id={`${formId}-name`}
                placeholder="dev"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            {error && <div className="text-meta text-err-text">{error}</div>}
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Cancel
            </DialogClose>
            <Button type="submit" disabled={busy || !name.trim()}>
              Create workspace
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function WorkspacesPage() {
  const me = useMe();
  const { workspaces, select } = useWorkspace();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [showOthers, setShowOthers] = useState(false);
  const [creating, setCreating] = useState(false);

  const mine = me.personal_workspace;
  const filter = query.trim().toLowerCase();
  const others = workspaces.filter((ws) => othersPersonal(ws, mine));
  const folded = !filter && !showOthers && others.length > 0;
  const visible = workspaces.filter(
    (ws) =>
      ws.name.toLowerCase().includes(filter) &&
      (!folded || !othersPersonal(ws, mine)),
  );
  const open = (name: string) => {
    select(name);
    navigate("/settings/workspace/evaluators");
  };

  return (
    <>
      <PageHeader
        title="Workspaces"
        description="Click a workspace to switch to it and open its settings. Everyone has a personal workspace; admins create shared ones and add their members."
        actions={
          me.is_admin && (
            <Button size="sm" onClick={() => setCreating(true)}>
              New workspace
            </Button>
          )
        }
      />
      <div className="page">
        <div className="toolbar mb-3">
          <Input
            className="w-full sm:w-64"
            placeholder="Filter workspaces"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {visible.length === 0 ? (
          <Card className="py-8">
            <Empty icon={SearchX} title="No workspaces match this filter" />
          </Card>
        ) : (
          <Card className="rows gap-0 py-0">
            {visible.map((ws) => (
              <WorkspaceRow key={ws.id} ws={ws} me={mine} onOpen={open} />
            ))}
            {folded && (
              <button
                type="button"
                className="row w-full text-left text-meta text-muted-foreground hover:bg-fill"
                onClick={() => setShowOthers(true)}
              >
                Show {others.length} other personal workspace
                {others.length === 1 ? "" : "s"}
              </button>
            )}
          </Card>
        )}
      </div>
      {creating && <NewWorkspaceDialog onClose={() => setCreating(false)} />}
    </>
  );
}
