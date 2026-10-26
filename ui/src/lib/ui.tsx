import { useState } from "react";
import type { ReactNode } from "react";
import { Editor } from "./Editor";
import { monacoTheme, useTheme } from "../shell/theme";
import { describeTerm } from "./glossary";

// Renders nothing for names outside the glossary (custom evaluators), so call
// sites can attach it unconditionally.
export function InfoTip({ term }: { term: string }) {
  const text = describeTerm(term);
  if (!text) return null;
  return (
    <span className="infotip" tabIndex={0} aria-label={text}>
      <svg
        width="11"
        height="11"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="6.6" stroke="currentColor" strokeWidth="1.3" />
        <path
          d="M8 7.4v3.4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="8" cy="4.9" r="0.9" fill="currentColor" />
      </svg>
      <span className="infotip-bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}

function CopyGlyph({ copied }: { copied: boolean }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {copied ? (
        <path d="m3 8.5 3.5 3.5L13 5" />
      ) : (
        <>
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
          <path d="M10.5 3.5h-7a1 1 0 0 0-1 1v7" />
        </>
      )}
    </svg>
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
      <CopyGlyph copied={copied} />
    </button>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-sm shrink-0"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "✓" : "copy"}
    </button>
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
        <span className="w-3 text-ink-4">{open ? "▾" : "▸"}</span>
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
    <div className="flex gap-1 border-b border-n-100">
      {tabs.map((t) => (
        <button
          key={t.id}
          className={`tab-button ${active === t.id ? "active" : ""}`}
          disabled={t.disabled}
          title={t.title}
          onClick={() => !t.disabled && onSelect(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
