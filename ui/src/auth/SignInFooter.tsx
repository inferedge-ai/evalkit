import type { ReactNode } from "react";
import { Separator } from "@/components/ui/separator";
import { useApi } from "../lib/query";
import type { CliInfo } from "../lib/types";

const DOCS_SITE_URL = "https://inferedge-ai.github.io/evalkit/";
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

export function SignInFooter() {
  const cli = useApi<CliInfo>("/cli");
  return (
    <footer className="flex flex-wrap items-center justify-center gap-3 p-4 text-xs text-muted-foreground">
      <FooterLink href={DOCS_SITE_URL}>Documentation</FooterLink>
      <Separator orientation="vertical" />
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
