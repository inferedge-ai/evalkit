// One fetch per session: /api/meta carries deploy-wide facts (configured
// agents, trace sources). The AuthGate loads it before the app renders, so
// every page can read it synchronously.

import { api } from "./api";
import { httpUrl } from "./safeUrl";
import type { Meta } from "./types";

const EMPTY: Meta = {
  agents: [],
  agent_repos: {},
};

let meta: Meta | null = null;

export async function loadMeta(): Promise<void> {
  meta = await api<Meta>("/meta").catch(() => EMPTY);
}

// /api/appearance needs no sign-in, so the login page shows the accent too.
export async function loadAccent(): Promise<void> {
  const { accent_color } = await api<{ accent_color: string | null }>(
    "/appearance",
  ).catch(() => ({ accent_color: null }));
  applyAccent(accent_color);
}

// The deploy's accent colour becomes --deploy-accent on <html>; theme.css
// draws it as a strip along the top of the page.
function applyAccent(color: string | null): void {
  const root = document.documentElement;
  if (color) {
    root.style.setProperty("--deploy-accent", color);
    root.dataset.accent = "";
  } else {
    root.style.removeProperty("--deploy-accent");
    delete root.dataset.accent;
  }
}

export function getMeta(): Meta {
  return meta ?? EMPTY;
}

// The agent's repo (from meta) + the experiment's git sha → a commit URL, when
// both are known. Null otherwise, so the caller renders a plain sha.
export function agentCommitUrl(
  agent: string | null | undefined,
  sha: string | null | undefined,
): string | null {
  if (!agent || !sha) return null;
  const repo = httpUrl(getMeta().agent_repos[agent]);
  return repo ? `${repo}/commit/${encodeURIComponent(sha)}` : null;
}

export function resetMetaForTests(value: Meta | null): void {
  meta = value;
}
