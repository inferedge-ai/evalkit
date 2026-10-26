// A case's fixtures are CaseFile entries {key, sha256, context}; bytes are
// content-addressed and download from /api/fixtures/sha256/<sha>, named after
// the key.
import type { CaseFile } from "./types";

export interface FixtureEntry {
  key: string;
  name: string;
  context: string;
  path: string;
}

export function fixturePath(sha256: string, name: string): string {
  return `/fixtures/sha256/${sha256}?name=${encodeURIComponent(name)}`;
}

export function fixtureEntries(
  files: CaseFile[] | null | undefined,
): FixtureEntry[] {
  return (files ?? []).map((file) => ({
    key: file.key,
    name: file.key,
    context: file.context,
    path: fixturePath(file.sha256, file.key),
  }));
}
