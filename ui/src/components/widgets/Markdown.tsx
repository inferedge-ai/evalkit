import { useMemo } from "react";
import type { ReactElement, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components, Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Highlight, themes } from "prism-react-renderer";
import "katex/dist/katex.min.css";
import { normalizeMathDelimiters } from "@/lib/format";
import { CopyButton } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { useTheme } from "@/shell/theme";
import "./markdown.css";

const MAX_HIGHLIGHTED_CHARS = 20_000;

function HighlightedCode({
  code,
  language,
}: {
  code: string;
  language: string;
}) {
  const { theme } = useTheme();
  const base = theme === "dark" ? themes.nightOwl : themes.github;
  return (
    <Highlight
      code={code}
      language={language}
      theme={{
        ...base,
        plain: { ...base.plain, backgroundColor: "var(--muted)" },
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
  );
}

function FencedCode({ code, language }: { code: string; language: string }) {
  return (
    <div className="code-block">
      <div className="code-header">
        <span className="language-label">
          {language === "text" ? "" : language}
        </span>
        <CopyButton text={code} />
      </div>
      {code.length > MAX_HIGHLIGHTED_CHARS ? (
        <pre className="code-pre">
          <code>{code}</code>
        </pre>
      ) : (
        <HighlightedCode code={code} language={language} />
      )}
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
    return <FencedCode code={code} language={language} />;
  },
  table({ node: _node, ...props }) {
    return (
      <div className="table-wrapper">
        <table {...props} />
      </div>
    );
  },
  a({ node: _node, href, ...props }) {
    if (href?.startsWith("#")) return <a href={href} {...props} />;
    return <a href={href} target="_blank" rel="noreferrer" {...props} />;
  },
};

export function Markdown({
  text,
  className,
  math = false,
  remarkPlugins,
  components,
}: {
  text: string;
  className?: string;
  math?: boolean;
  remarkPlugins?: Options["remarkPlugins"];
  components?: Components;
}) {
  const source = useMemo(
    () => (math ? normalizeMathDelimiters(text) : text),
    [math, text],
  );
  return (
    <div className={cn("markdown-content", className)}>
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          ...(math ? [remarkMath] : []),
          ...(remarkPlugins ?? []),
        ]}
        rehypePlugins={
          math ? [[rehypeKatex, { maxSize: 10, maxExpand: 100 }]] : []
        }
        components={{ ...BASE_COMPONENTS, ...components }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
