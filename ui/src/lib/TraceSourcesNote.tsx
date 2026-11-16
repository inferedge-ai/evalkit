import { TRACE_SOURCES_NOTE, TRACE_SOURCE_ISSUES_URL } from "./traceSources";

export function TraceSourcesNote() {
  return (
    <p className="max-w-3xl text-xs text-muted-foreground" role="note">
      {TRACE_SOURCES_NOTE}{" "}
      <a
        className="underline"
        href={TRACE_SOURCE_ISSUES_URL}
        target="_blank"
        rel="noreferrer"
      >
        Open an issue
      </a>
    </p>
  );
}
