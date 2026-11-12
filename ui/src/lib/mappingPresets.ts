export const FRAMEWORK_MAPPINGS_DOC = "/docs/framework-mappings";

export function formatPreset(document: Record<string, unknown>): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

export function replacedByPick(
  current: string,
  lastPicked: string | null,
): string | null {
  return current.trim() === "" || current === lastPicked ? null : current;
}
