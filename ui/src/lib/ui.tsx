import { useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Info,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Editor } from "./Editor";
import { monacoTheme, useTheme } from "../shell/theme";
import { describeTerm } from "./glossary";
import { Button } from "@/components/ui/button";
import { Tabs as TabsRoot, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Renders nothing for names outside the glossary (custom evaluators), so call
// sites can attach it unconditionally.
export function InfoTip({ term }: { term: string }) {
  const text = describeTerm(term);
  const [open, setOpen] = useState(false);
  const tapped = useRef(false);
  const descriptionId = useId();
  if (!text) return null;
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        type="button"
        delay={0}
        closeOnClick={false}
        aria-label="definition"
        aria-describedby={descriptionId}
        className="inline-flex shrink-0 cursor-help! items-center rounded-full text-ink-4 outline-none hover:text-brand focus-visible:text-brand focus-visible:ring-2 focus-visible:ring-ring/50"
        // Touch has no hover, so a tap toggles it; a mouse or keyboard press
        // keeps it open. In a sortable table header the press would also sort.
        onPointerDown={(e) => {
          tapped.current = e.pointerType === "touch";
        }}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(tapped.current ? !open : true);
          tapped.current = false;
        }}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <Info size={12} />
      </TooltipTrigger>
      <span id={descriptionId} hidden>
        {text}
      </span>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}

export function ToArrow() {
  return (
    <ArrowRight
      size={12}
      role="img"
      aria-label="to"
      className="inline shrink-0 align-[-0.125em]"
    />
  );
}

// An id you copy, not a button you press: the whole label is the hit area and
// the glyph is only there to say so.
export function CopyText({
  value,
  label,
  title,
}: {
  value: string;
  label?: string;
  title?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      // No size of its own: an id renders at whatever size its context uses,
      // from a breadcrumb crumb to a page heading.
      className="-mx-1 inline-flex min-w-0 items-center gap-1 rounded px-1 font-mono text-inherit transition-colors hover:bg-fill hover:text-brand"
      title={title ?? `copy ${value}`}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      <span className="truncate">{label ?? value}</span>
      {copied ? (
        <Check size={11} className="shrink-0" />
      ) : (
        <Copy size={11} className="shrink-0" />
      )}
    </button>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check size={12} role="img" aria-label="copied" /> : "copy"}
    </Button>
  );
}

export function Collapsible({
  header,
  badge,
  copyText,
  actions,
  defaultOpen = false,
  open: controlled,
  onOpenChange,
  children,
}: {
  header: ReactNode;
  badge: string;
  copyText?: string;
  actions?: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const open = controlled ?? own;
  const toggle = () => {
    setOwn(!open);
    onOpenChange?.(!open);
  };
  return (
    <div className={`collapsible ${open ? "is-open" : ""}`}>
      <div className="collapsible-header group/row" onClick={toggle}>
        {open ? (
          <ChevronDown size={12} className="shrink-0 text-ink-4" />
        ) : (
          <ChevronRight size={12} className="shrink-0 text-ink-4" />
        )}
        <span className={`badge badge-${badge}`}>{badge}</span>
        <span className="collapsible-title">{header}</span>
        {actions}
        {/* One copy button per row would stripe the right edge of the whole
            transcript — reveal it on the row the pointer is on. */}
        {copyText !== undefined && (
          <span className="opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
            <CopyButton text={copyText} />
          </span>
        )}
      </div>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

export function CodeBlock({
  value,
  language,
}: {
  value: string;
  language: string;
}) {
  const { theme } = useTheme();
  const height = Math.min(Math.max(value.split("\n").length, 2) * 18 + 10, 480);
  return (
    <Editor
      height={height}
      theme={monacoTheme(theme)}
      language={language}
      value={value}
      options={{
        readOnly: true,
        minimap: { enabled: false },
        fontSize: 12,
        fontFamily: "JetBrains Mono Variable, ui-monospace, monospace",
        padding: { top: 4, bottom: 4 },
        lineNumbers: "off",
        wordWrap: "on",
        scrollBeyondLastLine: false,
        renderLineHighlight: "none",
        overviewRulerLanes: 0,
        scrollbar: { alwaysConsumeMouseWheel: false },
      }}
    />
  );
}

export function JsonView({ value }: { value: unknown }) {
  return <CodeBlock value={JSON.stringify(value, null, 2)} language="json" />;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: { id: T; label: ReactNode; disabled?: boolean; title?: string }[];
  active: T;
  onSelect: (id: T) => void;
}) {
  return (
    <TabsRoot
      value={active}
      onValueChange={(id) => onSelect(id as T)}
      className="overflow-x-auto border-b pb-px"
    >
      <TabsList variant="line">
        {tabs.map((t) => (
          <TabsTrigger
            key={t.id}
            value={t.id}
            disabled={t.disabled}
            title={t.title}
          >
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </TabsRoot>
  );
}
