import { appHome } from "../lib/home";

export function safeNext(raw: string | null): string {
  if (raw === null) return appHome();
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return appHome();
  }
  if (raw.startsWith("/login")) return appHome();
  for (const ch of raw) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 || code === 0x7f) return appHome();
  }
  return raw;
}
