import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login } from "../lib/api";
import { loadProviders, session, type Providers } from "./oidc";

function LoginForm({ next }: { next: string }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <form onSubmit={submit}>
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
      <button className="btn btn-primary w-full justify-center" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export function LoginChoices({
  providers,
  next,
  onSso,
}: {
  providers: Providers;
  next: string;
  onSso: () => void;
}) {
  const [adminOpen, setAdminOpen] = useState(false);
  const oidc = providers.oidc;
  const formOpen = oidc === null || providers.password === "all" || adminOpen;
  return (
    <div className="card w-84 p-6">
      <h1 className="page-title">Evalkit</h1>
      <p className="mt-1 mb-5 text-meta text-ink-3">
        {oidc === null
          ? "Sign in with your evalkit account."
          : `Sign in with your ${oidc.name} account.`}
      </p>
      {oidc !== null && (
        <button
          type="button"
          className="btn btn-primary mb-4 w-full justify-center"
          onClick={onSso}
        >
          Continue with {oidc.name}
        </button>
      )}
      {formOpen ? (
        <LoginForm next={next} />
      ) : (
        <button
          type="button"
          className="text-meta text-ink-3 underline"
          onClick={() => setAdminOpen(true)}
        >
          Admin sign-in
        </button>
      )}
    </div>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rawNext = params.get("next") ?? "/";
  const next = rawNext.startsWith("/login") ? "/" : rawNext;

  useEffect(() => {
    loadProviders().then(setProviders, (e) => setError(String(e)));
  }, []);

  const startSso = async () => {
    const s = await session();
    if (s === null) return;
    await s.signinRedirect({ state: next });
  };

  const fallback: Providers = { password: "all", oidc: null };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      {providers === null && error === null ? (
        <div className="pane-status">Loading sign-in options…</div>
      ) : (
        <div>
          {error !== null && (
            <div className="card mb-3 w-84 p-3 text-meta text-err-text">
              {error}
            </div>
          )}
          <LoginChoices
            providers={providers ?? fallback}
            next={next}
            onSso={startSso}
          />
        </div>
      )}
    </div>
  );
}
