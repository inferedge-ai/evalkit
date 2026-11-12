import type { ReactNode } from "react";
import {
  Info,
  Lightbulb,
  OctagonAlert,
  Pencil,
  TriangleAlert,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { AdmonitionKind } from "@/lib/docMarkdown";
import { cn } from "@/lib/utils";

const KINDS: Record<
  AdmonitionKind,
  { icon: typeof Info; edge: string; tone: string }
> = {
  note: { icon: Pencil, edge: "border-l-brand", tone: "text-brand" },
  info: { icon: Info, edge: "border-l-brand", tone: "text-brand" },
  tip: { icon: Lightbulb, edge: "border-l-ok", tone: "text-ok" },
  warning: { icon: TriangleAlert, edge: "border-l-warn", tone: "text-warn" },
  danger: {
    icon: OctagonAlert,
    edge: "border-l-destructive",
    tone: "text-destructive",
  },
};

export function Admonition({
  kind,
  type,
  title,
  children,
}: {
  kind: AdmonitionKind;
  type: string;
  title: string;
  children?: ReactNode;
}) {
  const { icon: Icon, edge, tone } = KINDS[kind];
  return (
    <Alert
      role="note"
      data-admonition={kind}
      data-type={type}
      className={cn(
        "my-3 border-l-4 px-3 py-2.5 text-[length:inherit] leading-[inherit]",
        edge,
      )}
    >
      <Icon aria-hidden className={tone} />
      {title && (
        <AlertTitle className="font-semibold text-foreground">
          {title}
        </AlertTitle>
      )}
      <AlertDescription
        className={cn(
          "min-w-0 text-[length:inherit] text-foreground text-pretty [&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_p:not(:last-child)]:mb-0",
          !title && "row-span-2",
        )}
      >
        {children}
      </AlertDescription>
    </Alert>
  );
}
