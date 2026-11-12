import { Skeleton } from "@/components/ui/skeleton";
import { useState } from "react";
import type { FormEvent } from "react";
import type { Providers } from "../auth/oidc";
import { apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { refreshQueries, useApi } from "../lib/query";
import type { UserRow } from "../lib/types";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

type UserAction = "archive" | "restore" | "promote" | "demote";

const ROW_COLUMNS = "grid-cols-[minmax(0,1fr)_auto_auto_auto_auto]";

export function UsersAdminPage() {
  const listed = useApi<UserRow[]>("/users");
  const users = listed.data;
  const providers = useApi<Providers>("/auth/providers");
  const passwordsOff = providers.data?.password === "none";
  const showForm =
    !passwordsOff && (providers.data !== undefined || providers.error !== null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setActionError(null);
    try {
      await apiJson("/users", "POST", { username, password });
      setUsername("");
      setPassword("");
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const act = async (name: string, action: UserAction) => {
    setActionError(null);
    try {
      await apiJson(`/users/${encodeURIComponent(name)}/${action}`, "POST");
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <PageHeader
        title="Users"
        description="Users are archived, never deleted: archiving revokes their tokens, blocks login and archives their personal workspace."
      />
      <div className="page">
        {passwordsOff && (
          <p className="mb-3 text-meta text-muted-foreground">
            People join by signing in with{" "}
            {providers.data?.oidc?.name ?? "the identity provider"}. For CI, add
            a machine user:{" "}
            <code className="font-mono">
              evalkit users add &lt;name&gt; --subject &lt;sub&gt;
            </code>
            .
          </p>
        )}
        {showForm && (
          <form
            className="mb-3 flex flex-wrap items-center gap-2"
            onSubmit={add}
          >
            <Input
              className="w-full sm:w-48"
              placeholder="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
            <Input
              className="w-full sm:w-48"
              type="password"
              placeholder="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <Button type="submit">Add user</Button>
          </form>
        )}
        {actionError && (
          <div className="mb-3 text-meta text-err-text">{actionError}</div>
        )}
        {listed.error && (
          <div className="pane-status error">{listed.error.message}</div>
        )}
        {users === undefined && !listed.error && (
          <Card className="rows gap-0 py-0">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className={`row ${ROW_COLUMNS}`}>
                <Skeleton className="h-4" style={{ width: `${20 + i * 8}%` }} />
                <Skeleton className="h-4 w-12" />
                <Skeleton className="h-3 w-28" />
              </div>
            ))}
          </Card>
        )}
        {users && (
          <Card className="rows gap-0 py-0">
            {users.map((user) => (
              <div key={user.id} className={`row ${ROW_COLUMNS}`}>
                <span className="row-strong font-mono">{user.username}</span>
                <span className="flex gap-1">
                  {user.is_admin && <Badge variant="secondary">admin</Badge>}
                  {user.archived && <Badge variant="secondary">archived</Badge>}
                </span>
                <span className="row-meta num">{fmtWhen(user.created_at)}</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    act(user.username, user.is_admin ? "demote" : "promote")
                  }
                >
                  {user.is_admin ? "demote" : "make admin"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    act(user.username, user.archived ? "restore" : "archive")
                  }
                >
                  {user.archived ? "restore" : "archive"}
                </Button>
              </div>
            ))}
          </Card>
        )}
      </div>
    </>
  );
}
