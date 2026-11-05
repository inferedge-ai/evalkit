import { useEffect, useState } from "react";
import { apiJson } from "../lib/api";
import { ApiKeyField } from "../lib/ApiKeyField";
import type { ApiKeyValue } from "../lib/ApiKeyField";
import { ENDPOINT_NOTE } from "../lib/endpoint";
import type { LlmProviderInfo } from "../lib/types";

export function providerBody(
  name: string,
  baseUrl: string,
  apiKey: ApiKeyValue,
  current?: LlmProviderInfo,
): Record<string, unknown> {
  const body: Record<string, unknown> = current
    ? {}
    : { name: name.trim(), base_url: baseUrl.trim() };
  if (current && baseUrl.trim() !== current.base_url)
    body.base_url = baseUrl.trim();
  if (apiKey.key) body.api_key = apiKey.key;
  else if (apiKey.clear) body.api_key = null;
  return body;
}

export function LlmProviderForm({
  provider,
  onSaved,
  onClose,
}: {
  provider?: LlmProviderInfo;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(provider?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [apiKey, setApiKey] = useState<ApiKeyValue>({ key: "", clear: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const urlClearsKey =
    !!provider?.has_api_key &&
    baseUrl.trim() !== provider.base_url &&
    !apiKey.key;
  const missing = !baseUrl.trim() || (!provider && !name.trim());

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = providerBody(name, baseUrl, apiKey, provider);
      await (provider
        ? apiJson(
            `/llm-providers/${encodeURIComponent(provider.name)}`,
            "PATCH",
            body,
          )
        : apiJson("/llm-providers", "POST", body));
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <div
        className="dialog w-160"
        role="dialog"
        aria-label={provider ? "Edit LLM provider" : "New LLM provider"}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <span className="font-semibold">
            {provider ? "Edit LLM provider" : "New LLM provider"}
          </span>
          {provider && (
            <span className="row-meta font-mono">{provider.name}</span>
          )}
          <button
            className="btn btn-quiet btn-sm ml-auto"
            aria-label="close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="dialog-body">
          {!provider && (
            <div className="form-row">
              <span className="form-label">Name</span>
              <input
                className="input w-72"
                placeholder="openai"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          )}
          <div className="form-row">
            <span className="form-label">Base URL</span>
            <input
              className="input min-w-0 flex-1"
              placeholder="https://api.openai.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
          <p className="row-meta whitespace-normal">{ENDPOINT_NOTE}</p>
          <div className="form-row">
            <span className="form-label">API key</span>
            <ApiKeyField
              hasKey={provider?.has_api_key ?? false}
              value={apiKey}
              onChange={setApiKey}
            />
          </div>
          {urlClearsKey && (
            <p className="row-meta whitespace-normal text-err-text">
              Saving a new base URL removes the stored API key. Enter the key
              for the new URL above.
            </p>
          )}
          {error && <div className="text-meta text-err-text">{error}</div>}
        </div>
        <div className="dialog-foot">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || missing}
            onClick={save}
          >
            {provider ? "Save" : "Create provider"}
          </button>
        </div>
      </div>
    </div>
  );
}
