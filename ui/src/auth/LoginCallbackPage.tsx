import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { clearSignOut, register, signOutLocally } from "../lib/api";
import { PublicShell } from "../shell/PublicShell";
import { completeSignIn } from "./completeSignIn";
import { SignInCard } from "./LoginPage";
import { session } from "./oidc";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function CallbackStatus({ error }: { error: string | null }) {
  return (
    <PublicShell>
      <div className="flex min-h-full items-center justify-center p-4">
        <SignInCard>
          {error === null ? (
            <p className="text-sm text-muted-foreground">Completing sign-in…</p>
          ) : (
            <>
              <p className="text-sm text-err-text">{error}</p>
              <Link
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
                to="/login"
              >
                Try again
              </Link>
            </>
          )}
        </SignInCard>
      </div>
    </PublicShell>
  );
}

let exchange: Promise<{ next: string }> | null = null;

function exchangeOnce(): Promise<{ next: string }> {
  if (exchange === null) {
    const run = (async () => {
      const s = await session();
      if (s === null) throw new Error("single sign-on is not configured");
      return completeSignIn({ session: s, register });
    })().catch(async (err: unknown) => {
      await signOutLocally();
      throw err;
    });
    exchange = run;
    const clear = () => {
      exchange = null;
    };
    run.then(clear, clear);
  }
  return exchange;
}

export function LoginCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    exchangeOnce().then(
      ({ next }) => {
        clearSignOut();
        if (!cancelled) navigate(next, { replace: true });
      },
      (err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return <CallbackStatus error={error} />;
}
