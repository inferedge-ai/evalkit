import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import JsonWorker from "monaco-editor/language/json/json.worker.js?worker";

self.MonacoEnvironment = {
  getWorker: (_, label) =>
    label === "json" ? new JsonWorker() : new EditorWorker(),
};

loader.config({ monaco });

export { default } from "@monaco-editor/react";
