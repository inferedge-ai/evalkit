import { Link } from "react-router-dom";

export function NotFound({
  what,
  up,
}: {
  what: string;
  up: { label: string; to: string };
}) {
  return (
    <div className="page">
      <div className="card p-6">
        <h1 className="page-title mb-2">Not found</h1>
        <p className="text-lede text-ink-3">
          There is no <span className="font-mono">{what}</span>.
        </p>
        <Link className="btn btn-quiet mt-3 text-brand" to={up.to}>
          ← {up.label}
        </Link>
      </div>
    </div>
  );
}
