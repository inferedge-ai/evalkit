// One fetch per session: /api/meta carries deploy-wide facts (accent
// colour, configured agents). The AuthGate loads it before the
// app renders, so every page can read it synchronously.

import { api } from "./api";
import { httpUrl } from "./safeUrl";
import type { Meta } from "./types";

const EMPTY: Meta = {
  accent_color: null,
  agents: [],
  agent_repos: {},
};

let meta: Meta | null = null;

export async function loadMeta(): Promise<void> {
  if (meta) return;
  meta = await api<Meta>("/meta").catch(() => EMPTY);
  applyAccent(meta.accent_color);
}

// The deploy's accent colour becomes --accent on <html>; theme.css derives
// the brand, hover, ring and tint shades from it under [data-accent].
export function applyAccent(color: string | null): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (color) {
    root.style.setProperty("--accent", color);
    root.dataset.accent = "";
  } else {
    root.style.removeProperty("--accent");
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
