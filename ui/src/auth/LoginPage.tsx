import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login } from "../lib/api";

export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const rawNext = params.get("next") ?? "/";
  const next = rawNext.startsWith("/login") ? "/" : rawNext;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      <form onSubmit={submit} className="card w-84 p-6">
        <h1 className="page-title">Evalkit</h1>
        <p className="mt-1 mb-5 text-meta text-ink-3">
          Sign in with your evalkit account.
        </p>
        <label className="mb-3 block text-micro font-semibold tracking-wide text-ink-2 uppercase">
          Username
          <input
            className="input mt-1 block w-full"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            required
          />
        </label>
        <label className="mb-5 block text-micro font-semibold tracking-wide text-ink-2 uppercase">
          Password
          <input
            className="input mt-1 block w-full"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <div className="mb-3 text-meta text-err-text">{error}</div>}
        <button
          className="btn btn-primary w-full justify-center"
          disabled={busy}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
