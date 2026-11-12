import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Separator } from "@/components/ui/separator";
import { API, readApiError } from "../lib/api";
import type { CliInfo } from "../lib/types";

const REPO_URL = "https://github.com/inferedge-ai/evalkit";
const LINK_CLASS =
  "inline-flex items-center gap-1 underline-offset-2 hover:text-foreground hover:underline";

// mark-github-16 from Primer Octicons 19.42.0.
// Copyright (c) 2026 GitHub Inc., MIT licence.
function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3 fill-current">
      <path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656" />
    </svg>
  );
}

function FooterLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className={LINK_CLASS}>
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
  const cli = useQuery({ queryKey: ["cli", "public"], queryFn: fetchCliInfo });
  return (
    <footer className="flex flex-wrap items-center justify-center gap-3 p-4 text-xs text-muted-foreground">
      <Link to="/docs" className={LINK_CLASS}>
        Docs
      </Link>
      <Separator orientation="vertical" aria-hidden />
      <FooterLink href={REPO_URL}>
        <GitHubMark />
        GitHub
      </FooterLink>
      {cli.data && (
        <>
          <Separator orientation="vertical" aria-hidden />
          <VersionLabel cli={cli.data} />
        </>
      )}
    </footer>
  );
}
