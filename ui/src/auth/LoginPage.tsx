import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { isSignedOut, login } from "../lib/api";
import { PublicShell } from "../shell/PublicShell";
import { safeNext } from "./next";
import type { SignInState } from "./SignInLink";
import {
  loadProviders,
  ProvidersUnavailable,
  session,
  type OidcProviders,
  type Providers,
} from "./oidc";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";

export function SignInCard({
  description,
  children,
}: {
  description?: string;
  children: ReactNode;
}) {
  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <h1 className="text-base font-semibold">Evalkit</h1>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  );
}

function LoginForm({ next }: { next: string }) {
  const id = useId();
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
    <form className="flex flex-col gap-4" onSubmit={submit}>
      <Field>
        <FieldLabel htmlFor={`${id}-username`}>Username</FieldLabel>
        <Input
          id={`${id}-username`}
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
        />
      </Field>
      <Field>
        <FieldLabel htmlFor={`${id}-password`}>Password</FieldLabel>
        <Input
          id={`${id}-password`}
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </Field>
      {error && <p className="text-sm text-err-text">{error}</p>}
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

function ProviderSignIn({
  oidc,
  signedOut,
  onSso,
}: {
  oidc: OidcProviders | null;
  signedOut: boolean;
  onSso: (replace?: boolean) => Promise<void>;
}) {
  const handOver = oidc !== null && !signedOut;
  const [busy, setBusy] = useState(handOver);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const start = () => runSso(onSso, setBusy, setError);

  useEffect(() => {
    if (!handOver || started.current) return;
    started.current = true;
    runSso(() => onSso(true), setBusy, setError);
  }, [handOver, onSso]);

  return (
    <SignInCard>
      {oidc === null ? (
        <Button
          type="button"
          className="w-full"
          onClick={() => window.location.reload()}
        >
          Retry
        </Button>
      ) : busy ? (
        <p className="text-sm text-muted-foreground">Signing you in…</p>
      ) : error !== null ? (
        <>
          <p className="text-sm text-err-text">{error}</p>
          <Button type="button" className="w-full" onClick={start}>
            Try again
          </Button>
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            You're signed out of evalkit.
          </p>
          <Button type="button" className="w-full" onClick={start}>
            Sign in again
          </Button>
        </>
      )}
    </SignInCard>
  );
}

export function LoginChoices({
  providers,
  next,
  signedOut,
  onSso,
}: {
  providers: Providers;
  next: string;
  signedOut: boolean;
  onSso: (replace?: boolean) => Promise<void>;
}) {
  const [adminOpen, setAdminOpen] = useState(false);
  const [ssoBusy, setSsoBusy] = useState(false);
  const [ssoError, setSsoError] = useState<string | null>(null);
  const oidc = providers.oidc;
  if (providers.password === "none")
    return <ProviderSignIn oidc={oidc} signedOut={signedOut} onSso={onSso} />;
  const formOpen = oidc === null || providers.password === "all" || adminOpen;
  return (
    <SignInCard
      description={
        oidc === null
          ? "Sign in with your evalkit account."
          : `Sign in with your ${oidc.name} account.`
      }
    >
      {oidc !== null && (
        <Button
          type="button"
          className="w-full"
          disabled={ssoBusy}
          onClick={() => runSso(onSso, setSsoBusy, setSsoError)}
        >
          Continue with {oidc.name}
        </Button>
      )}
      {ssoError !== null && <p className="text-sm text-err-text">{ssoError}</p>}
      {formOpen ? (
        <LoginForm next={next} />
      ) : (
        <button
          type="button"
          className="self-start text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          onClick={() => setAdminOpen(true)}
        >
          Admin sign-in
        </button>
      )}
    </SignInCard>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fallbackPassword, setFallbackPassword] =
    useState<Providers["password"]>("all");

  const next = safeNext(params.get("next"));
  const prompt = params.get("prompt") || undefined;
  const clicked = (useLocation().state as SignInState | null)?.signIn === true;

  useEffect(() => {
    loadProviders().then(setProviders, (e) => {
      if (e instanceof ProvidersUnavailable && e.password !== null)
        setFallbackPassword(e.password);
      setError(e instanceof Error ? e.message : String(e));
    });
  }, []);

  const startSso = async (replace = false) => {
    const s = await session();
    if (s === null) throw new Error("Single sign-on is not configured.");
    await s.signinRedirect({
      state: next,
      prompt,
      redirectMethod: replace ? "replace" : "assign",
    });
  };

  const fallback: Providers = { password: fallbackPassword, oidc: null };

  return (
    <PublicShell>
      <div className="flex min-h-full items-center justify-center p-4">
        {providers === null && error === null ? (
          <div className="pane-status">Loading sign-in options…</div>
        ) : (
          <div className="flex w-full max-w-sm flex-col gap-3">
            {error !== null && (
              <Card size="sm" className="text-sm text-err-text">
                <CardContent>{error}</CardContent>
              </Card>
            )}
            <LoginChoices
              providers={providers ?? fallback}
              next={next}
              signedOut={isSignedOut() && !clicked}
              onSso={startSso}
            />
          </div>
        )}
      </div>
    </PublicShell>
  );
}
