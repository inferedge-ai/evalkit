import { ArrowLeft, FileQuestion } from "lucide-react";
import { Link } from "react-router-dom";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

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
        <Card className="items-center gap-4 py-8">
          <Empty
            icon={FileQuestion}
            title={
              <>
                There is no <span className="font-mono">{what}</span>
              </>
            }
          />
          <Link
            className={buttonVariants({ variant: "outline", size: "sm" })}
            to={up.to}
          >
            <ArrowLeft /> {up.label}
          </Link>
        </Card>
      </div>
    </>
  );
}
