export function safeNext(raw: string | null): string {
  if (raw === null) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  if (raw.startsWith("/login")) return "/";
  for (const ch of raw) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 || code === 0x7f) return "/";
  }
  return raw;
}
