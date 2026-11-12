import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  formatPreset,
  FRAMEWORK_MAPPINGS_DOC,
  restorableAfterPick,
} from "../lib/mappingPresets";
import { useApi } from "../lib/query";
import { Select } from "../lib/Select";
import type { MappingPreset } from "../lib/types";

export function MappingPresetPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (text: string) => void;
}) {
  const presets = useApi<MappingPreset[]>("/mapping-presets");
  const [picked, setPicked] = useState<{ name: string; text: string } | null>(
    null,
  );
  const [replaced, setReplaced] = useState<string | null>(null);

  const pick = (name: string) => {
    const preset = presets.data?.find((p) => p.name === name);
    if (!preset) return;
    const text = formatPreset(preset.document);
    setReplaced(restorableAfterPick(value, picked?.text ?? null, replaced));
    setPicked({ name, text });
    onChange(text);
  };

  const undo = () => {
    if (replaced === null) return;
    onChange(replaced);
    setReplaced(null);
    setPicked(null);
  };

  return (
    <div className="flex flex-col gap-1">
      <Select
        ariaLabel="mapping preset"
        placeholder="Start from a preset…"
        searchable
        disabled={!presets.data}
        value={picked && value === picked.text ? picked.name : ""}
        onChange={pick}
        options={(presets.data ?? []).map((p) => ({
          value: p.name,
          label: p.name,
        }))}
      />
      {presets.error && (
        <p className="text-meta text-err-text">{presets.error.message}</p>
      )}
      <p className="row-meta whitespace-normal">
        A preset fills the mapping below, and you can edit it.{" "}
        <a
          className="underline"
          href={FRAMEWORK_MAPPINGS_DOC}
          target="_blank"
          rel="noreferrer"
        >
          Which preset fits your framework
        </a>
      </p>
      {replaced !== null && picked && value === picked.text && (
        <div className="row-meta flex flex-wrap items-center gap-2 whitespace-normal">
          The {picked.name} preset replaced your edits.
          <Button variant="outline" size="sm" onClick={undo}>
            Undo
          </Button>
        </div>
      )}
    </div>
  );
}
