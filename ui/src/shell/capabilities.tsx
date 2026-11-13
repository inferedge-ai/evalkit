import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ProvidersUnavailable } from "../auth/oidc";
import { api, ApiError } from "../lib/api";
import { loadMeta } from "../lib/meta";
import { queryClient } from "../lib/query";
import type { Me } from "../lib/types";

export const MeContext = createContext<Me | null>(null);

const ClearMeContext = createContext<() => void>(() => {});

export function useClearMe(): () => void {
  return useContext(ClearMeContext);
}

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe outside AuthGate");
  return me;
}

export function useOptionalMe(): Me | null {
  return useContext(MeContext);
}

export function loginPath(location: { pathname: string; search: string }) {
  return `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
}

export function RequireMe() {
  const location = useLocation();
  if (!useOptionalMe()) return <Navigate to={loginPath(location)} replace />;
  return <Outlet />;
}

// Route-level auth gate: probes /auth/me once and renders its routes with or
// without a user; pages that need one sit under RequireMe. The login page lives
// OUTSIDE the gate, so an unauthenticated probe can never loop.
export function AuthGate() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<
    "loading" | "ok" | "unauthorized" | "error"
  >("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The gate mounts once per session (sign-in returns here), so nothing a
    // previous user loaded in this tab is shown to the next one.
    queryClient.clear();
    api<Me>("/auth/me")
      // Meta is auth-walled too, so it loads behind the same gate; pages then
      // read the agent list synchronously.
      .then(async (m) => {
        await loadMeta();
        setMe(m);
        setStatus("ok");
      })
      .catch((e) => {
        if (
          (e instanceof ApiError && e.status === 401) ||
          e instanceof ProvidersUnavailable
        ) {
          setStatus("unauthorized");
        } else {
          setError(String(e));
          setStatus("error");
        }
      });
  }, []);

  // Signing out sets this after the signed-in pages unmount, so clearing the
  // cache here can't make them refetch without a token.
  useEffect(() => {
    if (status === "unauthorized") queryClient.clear();
  }, [status]);

  useEffect(() => {
    document.title = "Evalkit";
  }, []);

  if (status === "loading")
    return <div className="pane-status">Checking session…</div>;
  if (status === "error")
    return <div className="pane-status error">{error}</div>;
  return (
    <ClearMeContext.Provider
      value={() => {
        setMe(null);
        setStatus("unauthorized");
      }}
    >
      <MeContext.Provider value={me}>
        <Outlet />
      </MeContext.Provider>
    </ClearMeContext.Provider>
  );
}
