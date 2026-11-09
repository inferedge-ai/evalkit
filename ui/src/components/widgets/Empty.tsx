import type { HTMLAttributes, ReactNode } from "react";
import { Info, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const ICON_SIZE = { sm: "size-4", md: "size-5" };
const ICON_PADDING = { sm: "p-2", md: "p-3" };

export function Empty({
  icon: Icon = Info,
  title,
  description,
  action,
  size = "md",
  className,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  size?: "sm" | "md";
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex grow flex-col items-center justify-center gap-3",
        className,
      )}
      {...rest}
    >
      <div
        aria-hidden
        className={cn(
          "flex items-center justify-center rounded-xl bg-accent text-muted-foreground",
          ICON_PADDING[size],
        )}
      >
        <Icon className={ICON_SIZE[size]} />
      </div>
      <div className="flex flex-col items-center gap-1 text-center">
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}
