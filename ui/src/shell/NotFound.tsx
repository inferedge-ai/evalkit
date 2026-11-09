import { Link } from "react-router-dom";
import { PageHeader } from "@/components/widgets/PageHeader";

export function NotFound({
  what,
  up,
}: {
  what: string;
  up: { label: string; to: string };
}) {
  return (
    <>
      <PageHeader title="Not found" />
      <div className="page">
        <div className="card p-6">
          <p className="text-lede text-ink-3">
            There is no <span className="font-mono">{what}</span>.
          </p>
          <Link className="btn btn-quiet mt-3 text-brand" to={up.to}>
            ← {up.label}
          </Link>
        </div>
      </div>
    </>
  );
}
