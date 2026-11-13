import { useMemo, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { Components, Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Highlight, themes } from "prism-react-renderer";
import "katex/dist/katex.min.css";
import { Button } from "@/components/ui/button";
import { normalizeMathDelimiters } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useTheme } from "@/shell/theme";
import "./markdown.css";

function CopyCodeButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="icon-sm"
      className="copy-button"
      aria-label="Copy code"
      title="Copy code"
      onClick={(e) => {
        e.stopPropagation();
        void navigator.clipboard.writeText(code).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        });
      }}
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  );
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const { theme } = useTheme();
  const base = theme === "dark" ? themes.nightOwl : themes.github;
  return (
    <div className="code-block-wrapper" data-language={language}>
      {language !== "text" && (
        <div className="code-header">
          <span className="language-label">{language}</span>
        </div>
      )}
      <div className="code-copy-sticky">
        <CopyCodeButton code={code} />
      </div>
      <Highlight
        code={code}
        language={language}
        theme={{
          ...base,
          plain: { ...base.plain, backgroundColor: "transparent" },
        }}
      >
        {({ className, style, tokens, getLineProps, getTokenProps }) => (
          <pre className={cn("code-pre", className)} style={style}>
            <code>
              {tokens.map((line, i) => (
                <div key={i} {...getLineProps({ line })}>
                  {line.map((token, key) => (
                    <span key={key} {...getTokenProps({ token })} />
                  ))}
                </div>
              ))}
            </code>
          </pre>
        )}
      </Highlight>
    </div>
  );
}

type CodeProps = { className?: string; children?: ReactNode };

const BASE_COMPONENTS: Components = {
  pre({ children }) {
    const props = (children as ReactElement<CodeProps> | undefined)?.props;
    const code = String(props?.children ?? "").replace(/\n$/, "");
    const language =
      /language-(\w+)/.exec(props?.className ?? "")?.[1] ?? "text";
    return <CodeBlock code={code} language={language} />;
  },
  table({ children }) {
    return (
      <div className="table-wrapper">
        <table>{children}</table>
      </div>
    );
  },
  a({ href, children }) {
    if (href?.startsWith("#")) return <a href={href}>{children}</a>;
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
};

export function Markdown({
  text,
  className,
  remarkPlugins = [],
  components,
}: {
  text: string;
  className?: string;
  remarkPlugins?: Options["remarkPlugins"];
  components?: Components;
}) {
  const normalized = useMemo(() => normalizeMathDelimiters(text), [text]);
  return (
    <div className={cn("markdown-content", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, ...(remarkPlugins ?? [])]}
        rehypePlugins={[rehypeKatex]}
        components={{ ...BASE_COMPONENTS, ...components }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
