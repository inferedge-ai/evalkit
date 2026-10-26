const API = import.meta.env.VITE_API_BASE_URL ?? "";

const TOKEN_KEY = "evalkit.token";

// Absent under test / SSR — the token simply lives in memory there.
const store: Storage | null =
  typeof localStorage !== "undefined" ? localStorage : null;

let token: string | null = store?.getItem(TOKEN_KEY) ?? null;

export function setToken(value: string | null): void {
  token = value;
  if (value === null) store?.removeItem(TOKEN_KEY);
  else store?.setItem(TOKEN_KEY, value);
}

export function hasToken(): boolean {
  return token !== null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class NotFoundError extends ApiError {
  constructor(message: string) {
    super(404, message);
  }
}

export function isNotFound(e: unknown): e is NotFoundError {
  return e instanceof NotFoundError;
}

export function isMissing(e: unknown): boolean {
  return e instanceof ApiError && [400, 404, 422].includes(e.status);
}

export function isExperimentId(id: string): boolean {
  return /^\d+$/.test(id);
}

export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d),
      )
      .join("; ");
  return JSON.stringify(detail);
}

let clockOffsetMs = 0;

export function serverNow(): number {
  return Date.now() + clockOffsetMs;
}

export function noteServerDate(date: string | null): void {
  const at = date ? Date.parse(date) : NaN;
  if (!isNaN(at)) clockOffsetMs = at - Date.now();
}

export async function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(`${API}/api${path}`, { ...init, headers });
  noteServerDate(resp.headers.get("Date"));
  return resp;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(path, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}) as Record<string, unknown>);
    const detail = detailText(body.detail ?? resp.statusText);
    if (resp.status === 404) throw new NotFoundError(detail);
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export async function apiJson<T>(
  path: string,
  method: string,
  body?: unknown,
): Promise<T> {
  return api<T>(path, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// For INLINE previews only (img/iframe src) — the whole blob lands in JS
// memory, so never use this for downloads, which can be large zips.
export async function apiBlobUrl(path: string): Promise<string> {
  const resp = await apiFetch(path);
  if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
  return URL.createObjectURL(await resp.blob());
}

// Bearer auth rules out a plain <a href>, and fetching into a Blob would hold
// the whole file in tab memory. A single-use ticket lets the browser stream
// the response straight to disk; the server names the file via
// Content-Disposition. The navigation opens in a helper tab so an error body
// (401 on a spent ticket, 404 on a pruned workspace) never replaces the app.
export async function downloadFile(path: string): Promise<void> {
  const { ticket } = await apiJson<{ ticket: string }>(
    "/auth/download-ticket",
    "POST",
  );
  const anchor = document.createElement("a");
  anchor.href = `${API}/api${path}${path.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`;
  anchor.target = "_blank";
  anchor.rel = "noopener";
  anchor.click();
}

export const qs = (params: Record<string, string | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
};

export async function login(username: string, password: string): Promise<void> {
  const resp = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await resp.json().catch(() => ({}) as Record<string, unknown>);
  if (!resp.ok)
    throw new ApiError(resp.status, String(body.detail ?? "login failed"));
  setToken(String(body.token));
}

export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
  setToken(null);
}
