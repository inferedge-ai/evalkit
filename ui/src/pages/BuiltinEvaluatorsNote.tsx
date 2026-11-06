import { useEffect, useState } from "react";
import { api } from "../lib/api";

export interface BuiltinEvaluatorInfo {
  name: string;
  uses_judge: boolean;
  local_only: boolean;
}

export function splitBuiltins(list: BuiltinEvaluatorInfo[]): {
  server: string[];
  local: string[];
} {
  return {
    server: list.filter((e) => !e.local_only).map((e) => e.name),
    local: list.filter((e) => e.local_only).map((e) => e.name),
  };
}

function Names({ names }: { names: string[] }) {
  return (
    <>
      {names.map((name, i) => (
        <span key={name}>
          {i > 0 && ", "}
          <code className="font-mono">{name}</code>
        </span>
      ))}
    </>
  );
}

export function BuiltinEvaluatorsNote() {
  const [list, setList] = useState<BuiltinEvaluatorInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<BuiltinEvaluatorInfo[]>("/builtin-evaluators")
      .then(setList)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  if (error) return <div className="pane-status error">{error}</div>;
  if (!list) return null;
  const { server, local } = splitBuiltins(list);
  return (
    <p className="row-meta mb-2 whitespace-normal" data-testid="builtin-note">
      Built-in evaluators are also available in this workspace:{" "}
      <Names names={server} /> (run on the evalkit server with its judge, or on
      your machine when you bring your own judge key), <Names names={local} />{" "}
      (always run on your machine).
    </p>
  );
}
