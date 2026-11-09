import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Crumb {
  label: string;
  to: string;
}

export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
}: {
  title: ReactNode;
  description?: string;
  breadcrumbs?: Crumb[];
  actions?: ReactNode;
}) {
  return (
    <header className="flex min-h-12 w-full shrink-0 flex-wrap items-center border-b bg-card px-3">
      <div className="flex min-w-0 grow flex-col justify-center gap-0.5 overflow-hidden p-2">
        {breadcrumbs && (
          <nav
            aria-label="Breadcrumb"
            className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
          >
            {breadcrumbs.map((crumb, i) => (
              <span
                key={crumb.to}
                className="flex min-w-0 items-center gap-1.5"
              >
                {i > 0 && <span aria-hidden>/</span>}
                <Link
                  className="truncate transition-colors hover:text-foreground"
                  to={crumb.to}
                >
                  {crumb.label}
                </Link>
              </span>
            ))}
          </nav>
        )}
        <h1 className="truncate text-base font-semibold">{title}</h1>
        {description && (
          <p
            className="w-0 min-w-full truncate text-xs text-muted-foreground"
            title={description}
          >
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2 p-2">{actions}</div>
      )}
    </header>
  );
}
