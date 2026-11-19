import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { safeNext } from "../auth/next";
import { httpUrl } from "../lib/safeUrl";
import type { DatasetMetadata } from "../lib/types";

const KNOWN = new Set(["description", "links", "license", "attribution"]);
const LINK_CLASS =
  "underline underline-offset-2 transition-colors hover:text-foreground";

// Metadata comes from a README anyone with write access can sync, so only
// http(s) links and paths inside this app become links.
export function MetadataLink({ label, url }: { label: string; url: string }) {
  if (url.startsWith("/") && safeNext(url) === url)
    return (
      <Link className={LINK_CLASS} to={url}>
        {label}
      </Link>
    );
  const external = httpUrl(url);
  if (external)
    return (
      <a
        className={LINK_CLASS}
        href={external}
        target="_blank"
        rel="noreferrer"
      >
        {label}
      </a>
    );
  return <span title={url}>{label}</span>;
}

export function DatasetAbout({ metadata }: { metadata: DatasetMetadata }) {
  const { description, attribution, license, links } = metadata;
  const others = Object.entries(metadata).filter(
    ([key, value]) => !KNOWN.has(key) && typeof value === "string",
  );
  if (
    !description &&
    !attribution &&
    !license &&
    !links?.length &&
    others.length === 0
  )
    return null;
  return (
    <section
      aria-label="About this dataset"
      className="mb-3 flex flex-col gap-1.5"
    >
      {description && <p className="text-sm">{description}</p>}
      {attribution && (
        <p className="text-xs text-muted-foreground">{attribution}</p>
      )}
      {(license || !!links?.length) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {license && (
            <Badge variant="secondary">
              {license.url ? (
                <MetadataLink label={license.name} url={license.url} />
              ) : (
                license.name
              )}
            </Badge>
          )}
          {links?.map((link) => (
            <MetadataLink key={link.url} label={link.label} url={link.url} />
          ))}
        </div>
      )}
      {others.length > 0 && (
        <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {others.map(([key, value]) => (
            <div key={key} className="flex gap-1">
              <dt>{key}:</dt>
              <dd className="text-foreground">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
