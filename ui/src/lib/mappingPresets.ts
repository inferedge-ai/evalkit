export const FRAMEWORK_MAPPINGS_DOC = "/docs/framework-mappings";

export function formatPreset(document: Record<string, unknown>): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

export function restorableAfterPick(
  current: string,
  pickedText: string | null,
  restorable: string | null,
): string | null {
  if (current === pickedText) return restorable;
  return current.trim() === "" ? null : current;
}
