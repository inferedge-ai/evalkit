import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Separator } from "@/components/ui/separator";
import { API, readApiError } from "../lib/api";
import type { CliInfo } from "../lib/types";

const REPO_URL = "https://github.com/inferedge-ai/evalkit";

function FooterLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="underline-offset-2 hover:text-foreground hover:underline"
    >
      {children}
    </a>
  );
}

function VersionLabel({ cli }: { cli: CliInfo }) {
  const label = `${cli.package} v${cli.version}`;
  if (!/^\d+\.\d+\.\d+$/.test(cli.version)) return <span>{label}</span>;
  return (
    <FooterLink href={`${REPO_URL}/releases/tag/v${cli.version}`}>
      {label}
    </FooterLink>
  );
}

// No bearer: getting one loads the sign-in options, which fail while the
// identity provider is down, and may start a token refresh.
async function fetchCliInfo(): Promise<CliInfo> {
  const resp = await fetch(`${API}/api/cli`);
  if (!resp.ok) throw await readApiError(resp);
  return resp.json();
}

export function SignInFooter() {
  const cli = useQuery({ queryKey: ["/cli"], queryFn: fetchCliInfo });
  return (
    <footer className="flex flex-wrap items-center justify-center gap-3 p-4 text-xs text-muted-foreground">
      <FooterLink href={REPO_URL}>GitHub</FooterLink>
      {cli.data && (
        <>
          <Separator orientation="vertical" />
          <VersionLabel cli={cli.data} />
        </>
      )}
    </footer>
  );
}
