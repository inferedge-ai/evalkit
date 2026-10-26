import { lazy, Suspense } from "react";
import type { EditorProps } from "@monaco-editor/react";

const MonacoEditor = lazy(() => import("./monacoEditor"));

export function Editor(props: EditorProps) {
  return (
    <Suspense fallback={<div style={{ height: props.height }} />}>
      <MonacoEditor {...props} />
    </Suspense>
  );
}
