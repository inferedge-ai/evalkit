import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { EXPERIMENT_LIST_LIMIT, listCapNote } from "../lib/experimentStatus";
import { Select } from "../lib/Select";
import type { SelectOption } from "../lib/Select";
import type { ExperimentListItem } from "../lib/types";

export function compareUrl(base: number, compare: number): string {
  return `/experiments/compare?base=${base}&compare=${compare}`;
}

export function compareOptions(
  experiments: ExperimentListItem[],
  current: number,
): SelectOption<number>[] {
  return experiments
    .filter((e) => e.id !== current)
    .map((e) => ({
      value: e.id,
      label: `#${e.number} · ${e.agent}${e.config.model ? ` · ${e.config.model.split("/").pop()}` : ""}`,
    }));
}

export function ComparePicker({
  dataset,
  experimentId,
}: {
  dataset: string;
  experimentId: number;
}) {
  const navigate = useNavigate();
  const [experiments, setExperiments] = useState<ExperimentListItem[]>([]);

  useEffect(() => {
    api<ExperimentListItem[]>(
      `/datasets/${encodeURIComponent(dataset)}/experiments?limit=${EXPERIMENT_LIST_LIMIT}`,
    )
      .then(setExperiments)
      .catch(() => setExperiments([]));
  }, [dataset]);

  const options = compareOptions(experiments, experimentId);
  if (options.length === 0) return null;
  return (
    <Select<number>
      ariaLabel="compare with"
      value={0}
      placeholder="Compare with…"
      title={`compare this experiment against another run of the same dataset${listCapNote(experiments.length) ? ` (${listCapNote(experiments.length)})` : ""}`}
      options={options}
      onChange={(base) => navigate(compareUrl(base, experimentId))}
    />
  );
}
