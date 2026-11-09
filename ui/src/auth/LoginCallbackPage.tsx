import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { register, signOutLocally } from "../lib/api";
import { completeSignIn } from "./completeSignIn";
import { session } from "./oidc";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

export function CallbackStatus({ error }: { error: string | null }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      <Card className="w-84">
        <CardContent>
          <h1 className="page-title">Evalkit</h1>
          {error === null ? (
            <p className="mt-1 text-meta text-ink-3">Completing sign-in…</p>
          ) : (
            <>
              <p className="mt-1 mb-4 text-meta text-err-text">{error}</p>
              <Link
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
                to="/login"
              >
                Back to sign-in
              </Link>
            </>
          )}
        </CardContent>
      </Card>
    </div>
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
