import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/widgets/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

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
        <Card>
          <CardContent>
            <p className="text-lede text-ink-3">
              There is no <span className="font-mono">{what}</span>.
            </p>
            <Link
              className={cn(
                buttonVariants({ variant: "ghost" }),
                "mt-3 text-brand",
              )}
              to={up.to}
            >
              <ArrowLeft size={14} /> {up.label}
            </Link>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
