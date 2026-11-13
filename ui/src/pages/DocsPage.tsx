import { Children, isValidElement, useEffect, useId, useState } from "react";
import type { ComponentProps, ReactElement, ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import type { ExtraProps } from "react-markdown";
import { Markdown } from "@/components/widgets/Markdown";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { ApiError } from "../lib/api";
import { useApi } from "../lib/query";
import {
  DOC_GROUPS,
  DOC_TABS,
  docRoute,
  fillTemplate,
  isDocTab,
} from "../lib/docs";
import { remarkDocMarkers, resolveDocImage } from "../lib/docMarkdown";
import type { CliInfo } from "../lib/types";
import { useOptionalMe } from "../shell/capabilities";

function DocLink({ href, children }: { href?: string; children?: ReactNode }) {
  const route = docRoute(href);
  if (route) return <Link to={route}>{children}</Link>;
  if (href?.startsWith("#")) return <Link to={href}>{children}</Link>;
  return <a href={href}>{children}</a>;
}

function DocImage({
  src,
  alt,
  node: _node,
  ...props
}: ComponentProps<"img"> & ExtraProps) {
  return <img {...props} src={resolveDocImage(src)} alt={alt ?? ""} />;
}

function DocDiv({
  node,
  children,
  ...props
}: ComponentProps<"div"> & ExtraProps) {
  if (!("data-doc-tabs" in props)) return <div {...props}>{children}</div>;
  const labels = (node?.children ?? []).flatMap((child) =>
    child.type === "element" ? [String(child.properties.dataDocTab ?? "")] : [],
  );
  const panels = Children.toArray(children).filter(
    isValidElement,
  ) as ReactElement<{ children?: ReactNode }>[];
  return (
    <Tabs defaultValue={0} className="my-3">
      <TabsList>
        {labels.map((label, i) => (
          <TabsTrigger key={i} value={i} className="px-3">
            {label}
          </TabsTrigger>
        ))}
      </TabsList>
      {panels.map((panel, i) => (
        <TabsContent key={i} value={i} className="text-[length:inherit]">
          {panel.props.children}
        </TabsContent>
      ))}
    </Tabs>
  );
}

function useScrollToHash(tab: string | undefined) {
  const { hash } = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(hash.slice(1));
    const target = id ? document.getElementById(id) : null;
    if (!target) return;
    const isRow = target.tagName === "TR";
    target.scrollIntoView({ block: isRow ? "center" : "start" });
    if (!isRow) return;
    target.setAttribute("data-targeted", "");
    const timer = window.setTimeout(
      () => target.removeAttribute("data-targeted"),
      2000,
    );
    return () => {
      window.clearTimeout(timer);
      target.removeAttribute("data-targeted");
    };
  }, [hash, tab]);
}

function DocsMenu({ tab }: { tab: string }) {
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === tab;
  const listId = useId();
  const current = DOC_TABS.find((t) => t.id === tab);
  return (
    <nav
      className="md:sticky md:top-4 md:w-44 md:shrink-0 md:self-start"
      aria-label="Docs"
    >
      <Button
        variant="outline"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpenOn(open ? null : tab)}
        className="h-9 w-full justify-start gap-2 bg-card px-3 text-left font-normal md:hidden"
      >
        <span className="text-muted-foreground">{current?.group}</span>
        <span aria-hidden className="text-muted-foreground">
          /
        </span>
        <span className="min-w-0 flex-1 truncate font-medium">
          {current?.label}
        </span>
        <ChevronDown
          aria-hidden
          className={cn("size-4 transition-transform", open && "rotate-180")}
        />
      </Button>
      <div
        id={listId}
        className={cn(
          "mt-2 rounded-md border bg-card p-2 md:mt-0 md:block md:border-0 md:bg-transparent md:p-0",
          !open && "hidden",
        )}
      >
        {DOC_GROUPS.map((group) => (
          <div key={group} className="mb-4 last:mb-0">
            <div className="px-2 pb-1 text-xs font-medium text-muted-foreground">
              {group}
            </div>
            <ul className="flex flex-col gap-px">
              {DOC_TABS.filter((t) => t.group === group).map((t) => (
                <li key={t.id}>
                  <Link
                    to={`/docs/${t.id}`}
                    onClick={() => setOpenOn(null)}
                    aria-current={t.id === tab ? "page" : undefined}
                    className={cn(
                      "block rounded-md px-2 py-1.5 text-sm transition-colors",
                      t.id === tab
                        ? "bg-muted font-medium text-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {t.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

export function DocsPage() {
  const { tab } = useParams();
  useScrollToHash(tab);
  const me = useOptionalMe();
  const cli = useApi<CliInfo>("/cli");
  const error =
    cli.error instanceof ApiError
      ? cli.error.message
      : cli.error
        ? String(cli.error)
        : null;

  if (!isDocTab(tab)) return <Navigate to="/docs/quickstart" replace />;
  const values = {
    origin: window.location.origin,
    version: cli.data?.version ?? "<version>",
  };
  const body = DOC_TABS.find((t) => t.id === tab)?.body ?? "";
  return (
    <>
      {me && <PageHeader title="Docs" />}
      <div className="page">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 md:flex-row md:gap-10">
          <DocsMenu tab={tab} />
          <article className="min-w-0 flex-1">
            {error && <p className="text-ink-2">{error}</p>}
            <Markdown
              text={fillTemplate(body, values)}
              className="mx-auto max-w-3xl"
              remarkPlugins={[remarkDocMarkers]}
              components={{ a: DocLink, div: DocDiv, img: DocImage }}
            />
          </article>
        </div>
      </div>
    </>
  );
}
