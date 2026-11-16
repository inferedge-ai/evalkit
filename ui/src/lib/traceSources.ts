export const TRACE_SOURCES_NOTE =
  "evalkit imports traces from trace sources. Supported today: Phoenix and Langfuse. For another, file a GitHub issue.";

export const TRACE_SOURCE_ISSUES_URL =
  "https://github.com/inferedge-ai/evalkit/issues";

export type TraceProvider = "phoenix" | "langfuse";

export const PROVIDER_LABELS: Record<TraceProvider, string> = {
  phoenix: "Phoenix",
  langfuse: "Langfuse",
};

export const KEY_PLACEHOLDERS: Record<TraceProvider, string> = {
  phoenix: "API key",
  langfuse: "public key:secret key",
};

export const FILTER_DOCS: Partial<Record<TraceProvider, string>> = {
  langfuse:
    "https://langfuse.com/docs/api-and-data-platform/features/observations-api#observations-filters",
};
