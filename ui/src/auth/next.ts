export function safeNext(raw: string | null): string {
  if (raw === null) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  if (raw.startsWith("/login")) return "/";
  return raw;
}
