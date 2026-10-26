import { Link } from "react-router-dom";
import { CopyText } from "../lib/ui";

export interface Crumb {
  label: string;
  to?: string;
  /** When set, the crumb copies this value on click instead of navigating. */
  copy?: string;
  title?: string;
}

// Experiments live at a flat /experiments/:id — experiment ids are global and
// every existing link relies on it — so the parent dataset is not in the URL. This is where it comes from instead.
export function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav className="flex min-w-0 items-center gap-1.5 text-meta text-ink-3">
      {items.map((item, i) => (
        <span
          key={`${item.label}-${i}`}
          className="flex min-w-0 items-center gap-1.5"
        >
          {i > 0 && <span aria-hidden>/</span>}
          {item.copy ? (
            <CopyText value={item.copy} label={item.label} title={item.title} />
          ) : item.to ? (
            <Link
              className="truncate transition-colors hover:text-brand"
              to={item.to}
              title={item.title}
            >
              {item.label}
            </Link>
          ) : (
            <span className="truncate text-ink-2" title={item.title}>
              {item.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
