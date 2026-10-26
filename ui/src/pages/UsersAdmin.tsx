import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import type { UserRow } from "../lib/types";

type UserAction = "archive" | "restore" | "promote" | "demote";

export function UsersAdminPage() {
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api<UserRow[]>("/users")
      .then(setUsers)
      .catch((e) => setError(String(e)));
  };

  useEffect(refresh, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await apiJson("/users", "POST", { username, password });
      setUsername("");
      setPassword("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const act = async (name: string, action: UserAction) => {
    setError(null);
    try {
      await apiJson(`/users/${encodeURIComponent(name)}/${action}`, "POST");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Users</h1>
      <p className="row-meta mt-1">
        Users are archived, never deleted: archiving revokes their tokens,
        blocks login and archives their personal workspace.
      </p>
      <form className="toolbar mt-3 mb-3" onSubmit={add}>
        <input
          className="input"
          placeholder="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <input
          className="input"
          type="password"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <button className="btn btn-primary">Add user</button>
        {error && <span className="text-meta text-err-text">{error}</span>}
      </form>
      {!users ? (
        <div className="card rows">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="row grid-cols-[1fr_auto]">
              <span
                className="skeleton h-4"
                style={{ width: `${20 + i * 8}%` }}
              />
              <span className="skeleton h-3 w-24" />
            </div>
          ))}
        </div>
      ) : (
        <div className="card">
          <div className="rows">
            {users.map((user) => (
              <div
                key={user.id}
                className="row grid-cols-[minmax(0,1fr)_auto_auto_auto_auto]"
              >
                <span className="font-mono">{user.username}</span>
                <span className="flex gap-1">
                  {user.is_admin && <span className="chip muted">admin</span>}
                  {user.archived && (
                    <span className="chip muted">archived</span>
                  )}
                </span>
                <span className="row-meta num">{fmtWhen(user.created_at)}</span>
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    act(user.username, user.is_admin ? "demote" : "promote")
                  }
                >
                  {user.is_admin ? "demote" : "make admin"}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    act(user.username, user.archived ? "restore" : "archive")
                  }
                >
                  {user.archived ? "restore" : "archive"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
