import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login } from "../lib/api";
import { PublicShell } from "../shell/PublicShell";
import { safeNext } from "./next";
import { loadProviders, session, type Providers } from "./oidc";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
        <Input
          className="mt-1 block w-full"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
        />
      </label>
      <label className="mb-5 block text-micro font-semibold tracking-wide text-ink-2 uppercase">
        Password
        <Input
          className="mt-1 block w-full"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error && <div className="mb-3 text-meta text-err-text">{error}</div>}
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

export function runSso(
  onSso: () => Promise<void>,
  setBusy: (busy: boolean) => void,
  setError: (message: string | null) => void,
): void {
  setBusy(true);
  setError(null);
  onSso().catch((err) => {
    setError(err instanceof Error ? err.message : String(err));
    setBusy(false);
  });
}

export function LoginChoices({
  providers,
  next,
  onSso,
}: {
  providers: Providers;
  next: string;
  onSso: () => Promise<void>;
}) {
  const [adminOpen, setAdminOpen] = useState(false);
  const [ssoBusy, setSsoBusy] = useState(false);
  const [ssoError, setSsoError] = useState<string | null>(null);
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
        <Button
          type="button"
          className="mb-4 w-full"
          disabled={ssoBusy}
          onClick={() => runSso(onSso, setSsoBusy, setSsoError)}
        >
          Continue with {oidc.name}
        </Button>
      )}
      {ssoError !== null && (
        <div className="mb-3 text-meta text-err-text">{ssoError}</div>
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

  const next = safeNext(params.get("next"));

  useEffect(() => {
    loadProviders().then(setProviders, (e) =>
      setError(e instanceof Error ? e.message : String(e)),
    );
  }, []);

  const startSso = async () => {
    const s = await session();
    if (s === null) throw new Error("Single sign-on is not configured.");
    await s.signinRedirect({ state: next });
  };

  const fallback: Providers = { password: "all", oidc: null };

  return (
    <PublicShell>
      <div className="flex min-h-full items-center justify-center bg-canvas">
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
    </PublicShell>
  );
}
