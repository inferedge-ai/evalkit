import { useEffect, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Highlight, themes } from "prism-react-renderer";
import { api, ApiError } from "../lib/api";
import {
  DOC_GROUPS,
  DOC_TABS,
  docRoute,
  fillTemplate,
  isDocTab,
} from "../lib/docs";
import type { CliInfo } from "../lib/types";
import { CopyButton } from "../lib/ui";
import { useTheme } from "../shell/theme";

function Snippet({ code, language }: { code: string; language: string }) {
  const { theme } = useTheme();
  return (
    <div className="relative my-2">
      <div className="absolute top-1 right-1">
        <CopyButton text={code} />
      </div>
      <Highlight
        code={code}
        language={language}
        theme={theme === "dark" ? themes.nightOwl : themes.github}
      >
        {({ className, style, tokens, getLineProps, getTokenProps }) => (
          <pre
            className={`${className} overflow-x-auto rounded-md p-3 pr-16 font-mono text-meta`}
            style={style}
          >
            {tokens.map((line, i) => (
              <div key={i} {...getLineProps({ line })}>
                {line.map((token, key) => (
                  <span key={key} {...getTokenProps({ token })} />
                ))}
              </div>
            ))}
          </pre>
        )}
      </Highlight>
    </div>
  );
}

type CodeProps = { className?: string; children?: ReactNode };

function DocLink({ href, children }: { href?: string; children?: ReactNode }) {
  const route = docRoute(href);
  if (route) return <Link to={route}>{children}</Link>;
  return <a href={href}>{children}</a>;
}

function Pre({ children }: { children?: ReactNode }) {
  const child = children as ReactElement<CodeProps> | undefined;
  const props = child?.props ?? {};
  const code = String(props.children ?? "").replace(/\n$/, "");
  const language = /language-(\w+)/.exec(props.className ?? "")?.[1] ?? "text";
  return <Snippet code={code} language={language} />;
}

export function DocsPage() {
  const { tab } = useParams();
  const [info, setInfo] = useState<CliInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<CliInfo>("/cli")
      .then(setInfo)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  if (!isDocTab(tab)) return <Navigate to="/docs/quickstart" replace />;
  const values = {
    origin: window.location.origin,
    version: info?.version ?? "<version>",
  };
  const body = DOC_TABS.find((t) => t.id === tab)?.body ?? "";
  return (
    <div className="page">
      <div className="mx-auto flex max-w-6xl gap-10">
        <nav
          className="sticky top-4 w-44 shrink-0 self-start"
          aria-label="Docs"
        >
          <h1 className="page-title mb-3">Docs</h1>
          {DOC_GROUPS.map((group) => (
            <div key={group} className="mb-4">
              <div className="mb-1 text-micro font-semibold tracking-wide text-ink-3 uppercase">
                {group}
              </div>
              <ul className="flex flex-col">
                {DOC_TABS.filter((t) => t.group === group).map((t) => (
                  <li key={t.id}>
                    <Link
                      to={`/docs/${t.id}`}
                      aria-current={t.id === tab ? "page" : undefined}
                      className={`block rounded px-2 py-1 text-lede ${
                        t.id === tab
                          ? "bg-n-50 font-medium text-ink"
                          : "text-ink-2 hover:bg-hover"
                      }`}
                    >
                      {t.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <article className="min-w-0 flex-1">
          {error && <p className="text-ink-2">{error}</p>}
          <div className="md-body docs-body mx-auto max-w-3xl text-lede">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{ pre: Pre, a: DocLink }}
            >
              {fillTemplate(body, values)}
            </ReactMarkdown>
          </div>
        </article>
      </div>
    </div>
  );
}
