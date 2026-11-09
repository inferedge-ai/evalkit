import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiJson } from "../lib/api";
import { ApiKeyField } from "../lib/ApiKeyField";
import type { ApiKeyValue } from "../lib/ApiKeyField";
import { ENDPOINT_NOTE, PERSONAL_KEY_NOTE } from "../lib/endpoint";
import { FORM_DIALOG, FORM_DIALOG_BODY } from "../lib/formDialog";
import { Select } from "../lib/Select";
import type { RemoteEvaluatorInfo, WorkspaceInfo } from "../lib/types";
import { Input } from "@/components/ui/input";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";

export interface EvaluatorFormValues {
  name: string;
  kind: "score" | "gauge";
  endpoint: string;
  timeout: string;
  apiKey: ApiKeyValue;
}

export function evaluatorFormErrors(
  values: EvaluatorFormValues,
  creating: boolean,
): string[] {
  const errors: string[] = [];
  if (creating && !values.name.trim()) errors.push("Name is required.");
  if (creating && values.name.includes("@"))
    errors.push("Name cannot contain @.");
  if (!values.endpoint.trim()) errors.push("Endpoint is required.");
  const timeout = Number(values.timeout.trim());
  if (!values.timeout.trim() || !Number.isInteger(timeout) || timeout < 1)
    errors.push("Timeout must be a whole number of at least 1.");
  return errors;
}

export function evaluatorBody(
  values: EvaluatorFormValues,
  workspace: string,
  current?: RemoteEvaluatorInfo,
): Record<string, unknown> {
  const settings = {
    endpoint: values.endpoint.trim(),
    timeout_seconds: Number(values.timeout.trim()),
  };
  const body: Record<string, unknown> = current
    ? { settings }
    : { workspace, name: values.name.trim(), kind: values.kind, settings };
  if (values.apiKey.key) body.api_key = values.apiKey.key;
  else if (values.apiKey.clear) body.api_key = null;
  return body;
}

export function EvaluatorForm({
  ws,
  evaluator,
  onSaved,
  onClose,
}: {
  ws: WorkspaceInfo;
  evaluator?: RemoteEvaluatorInfo;
  onSaved: () => void;
  onClose: () => void;
}) {
  const formId = useId();
  const [values, setValues] = useState<EvaluatorFormValues>({
    name: evaluator?.name ?? "",
    kind: evaluator?.kind ?? "score",
    endpoint: evaluator?.settings.endpoint ?? "",
    timeout: String(evaluator?.settings.timeout_seconds ?? 120),
    apiKey: { key: "", clear: false },
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errors = evaluatorFormErrors(values, !evaluator);
  const set = <K extends keyof EvaluatorFormValues>(
    key: K,
    value: EvaluatorFormValues[K],
  ) => setValues((v) => ({ ...v, [key]: value }));

  const shared = ws.type === "shared";
  const urlClearsKey =
    !!evaluator?.has_api_key &&
    values.endpoint.trim() !== evaluator.settings.endpoint &&
    !values.apiKey.key;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = evaluatorBody(values, ws.name, evaluator);
      await (evaluator
        ? apiJson(
            `/evaluators/${encodeURIComponent(evaluator.ref)}`,
            "PATCH",
            body,
          )
        : apiJson("/evaluators", "POST", body));
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={FORM_DIALOG}>
        <DialogHeader>
          <DialogTitle>
            {evaluator ? "Edit evaluator" : "New evaluator"}
          </DialogTitle>
          <DialogDescription className="font-mono">
            {evaluator ? evaluator.ref : `@${ws.name}`}
          </DialogDescription>
        </DialogHeader>
        <div className={FORM_DIALOG_BODY}>
          {!evaluator && (
            <>
              <Field>
                <FieldLabel htmlFor={`${formId}-name`}>Name</FieldLabel>
                <Input
                  id={`${formId}-name`}
                  aria-label="name"
                  value={values.name}
                  onChange={(e) => set("name", e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>Kind</FieldLabel>
                <Select
                  ariaLabel="kind"
                  value={values.kind}
                  onChange={(kind) => set("kind", kind)}
                  options={[
                    { value: "score", label: "score" },
                    { value: "gauge", label: "gauge" },
                  ]}
                />
              </Field>
            </>
          )}
          <Field>
            <FieldLabel htmlFor={`${formId}-endpoint`}>Endpoint</FieldLabel>
            <Input
              id={`${formId}-endpoint`}
              aria-label="endpoint"
              placeholder="https://evaluators.example.com/evaluate"
              value={values.endpoint}
              onChange={(e) => set("endpoint", e.target.value)}
            />
            <FieldDescription>{ENDPOINT_NOTE}</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${formId}-timeout-s`}>Timeout (s)</FieldLabel>
            <Input
              id={`${formId}-timeout-s`}
              aria-label="timeout"
              inputMode="numeric"
              value={values.timeout}
              onChange={(e) => set("timeout", e.target.value)}
            />
          </Field>
          {shared ? (
            <Field>
              <FieldLabel>API key</FieldLabel>
              <ApiKeyField
                hasKey={evaluator?.has_api_key ?? false}
                value={values.apiKey}
                onChange={(apiKey) => set("apiKey", apiKey)}
              />
            </Field>
          ) : (
            <p className="row-meta whitespace-normal">{PERSONAL_KEY_NOTE}</p>
          )}
          {urlClearsKey && (
            <p className="row-meta whitespace-normal text-err-text">
              Saving a new endpoint removes the stored API key. Enter the key
              for the new endpoint above.
            </p>
          )}
          {errors.map((message) => (
            <div key={message} className="text-meta text-err-text">
              {message}
            </div>
          ))}
          {error && <div className="text-meta text-err-text">{error}</div>}
        </div>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button disabled={busy || errors.length > 0} onClick={save}>
            {evaluator ? "Save" : "Create evaluator"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
