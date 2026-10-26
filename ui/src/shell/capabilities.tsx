import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { loadMeta } from "../lib/meta";
import type { Me } from "../lib/types";

const Ctx = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(Ctx);
  if (!me) throw new Error("useMe outside AuthGate");
  return me;
}

// Route-level auth gate: everything behind it renders only once the /auth/me
// probe succeeds. The login page lives OUTSIDE the gate, so an unauthenticated
// probe can never loop.
export function AuthGate() {
  const location = useLocation();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<
    "loading" | "ok" | "unauthorized" | "error"
  >("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Me>("/auth/me")
      // Meta is auth-walled too, so it loads behind the same gate; pages then
      // read the accent / agent list synchronously.
      .then(async (m) => {
        await loadMeta();
        setMe(m);
        setStatus("ok");
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setStatus("unauthorized");
        } else {
          setError(String(e));
          setStatus("error");
        }
      });
  }, []);

  useEffect(() => {
    document.title = "Evalkit";
  }, []);

  if (status === "loading")
    return <div className="pane-status">Checking session…</div>;
  if (status === "unauthorized") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (status === "error")
    return <div className="pane-status error">{error}</div>;
  return (
    <Ctx.Provider value={me}>
      <Outlet />
    </Ctx.Provider>
  );
}
