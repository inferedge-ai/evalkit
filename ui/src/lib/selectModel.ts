export interface SelectOption<T extends string | number> {
  value: T;
  label: string;
  hint?: string | number;
  disabled?: boolean;
}

export function filterOptions<T extends string | number>(
  options: SelectOption<T>[],
  filter: string,
): SelectOption<T>[] {
  const needle = filter.trim().toLowerCase();
  if (!needle) return options;
  return options.filter((o) => o.label.toLowerCase().includes(needle));
}

export function nextIndex<T extends string | number>(
  options: SelectOption<T>[],
  from: number,
  step: 1 | -1,
): number {
  for (let i = from + step; i >= 0 && i < options.length; i += step) {
    if (!options[i].disabled) return i;
  }
  return from;
}

export function typeaheadIndex<T extends string | number>(
  options: SelectOption<T>[],
  from: number,
  key: string,
): number {
  const k = key.toLowerCase();
  for (let n = 1; n <= options.length; n++) {
    const i = (from + n) % options.length;
    const o = options[i];
    if (!o.disabled && o.label.toLowerCase().startsWith(k)) return i;
  }
  return -1;
}
