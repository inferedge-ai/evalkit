import { useApi } from "../lib/query";

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
  const builtins = useApi<BuiltinEvaluatorInfo[]>("/builtin-evaluators");
  if (builtins.error)
    return <div className="pane-status error">{builtins.error.message}</div>;
  if (!builtins.data) return null;
  const { server, local } = splitBuiltins(builtins.data);
  return (
    <p className="row-meta mb-2 whitespace-normal" data-testid="builtin-note">
      Built-in evaluators are also available in this workspace:{" "}
      <Names names={server} /> (run on the evalkit server with its judge, or on
      your machine when you bring your own judge key), <Names names={local} />{" "}
      (always run on your machine).
    </p>
  );
}
