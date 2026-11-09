import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { filterOptions, nextIndex, typeaheadIndex } from "./selectModel";
import type { SelectOption } from "./selectModel";

export type { SelectOption };

interface CommonProps<T extends string | number> {
  options: SelectOption<T>[];
  placeholder?: string;
  title?: string;
  ariaLabel?: string;
  className?: string;
  searchable?: boolean;
  disabled?: boolean;
  more?: { label: string; options: SelectOption<T>[] };
}

export function Select<T extends string | number>({
  value,
  onChange,
  ...common
}: CommonProps<T> & { value: T; onChange: (value: T) => void }) {
  const current = [...common.options, ...(common.more?.options ?? [])].find(
    (o) => o.value === value,
  );
  return (
    <SelectMenu
      {...common}
      focusValue={value}
      isSelected={(v) => v === value}
      onPick={onChange}
      closeOnPick
      label={current?.label ?? null}
    />
  );
}

export function MultiSelect<T extends string | number>({
  values,
  onChange,
  ...common
}: CommonProps<T> & { values: T[]; onChange: (values: T[]) => void }) {
  const chosen = new Set(values);
  const labels = [...common.options, ...(common.more?.options ?? [])]
    .filter((o) => chosen.has(o.value))
    .map((o) => o.label);
  return (
    <SelectMenu
      {...common}
      focusValue={values[0]}
      isSelected={(v) => chosen.has(v)}
      onPick={(v) =>
        onChange(chosen.has(v) ? values.filter((x) => x !== v) : [...values, v])
      }
      closeOnPick={false}
      label={labels.length ? labels.join(", ") : null}
      multiselectable
    />
  );
}

function SelectMenu<T extends string | number>({
  options,
  placeholder = "Select…",
  title,
  ariaLabel,
  className = "",
  searchable,
  disabled = false,
  more,
  focusValue,
  isSelected,
  onPick,
  closeOnPick,
  label,
  multiselectable = false,
}: CommonProps<T> & {
  focusValue: T | undefined;
  isSelected: (value: T) => boolean;
  onPick: (value: T) => void;
  closeOnPick: boolean;
  label: string | null;
  multiselectable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState("");
  const [hi, setHi] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const extra = more?.options ?? [];
  const every = [...options, ...extra];
  const withSearch = searchable ?? every.length > 10;
  const listed = expanded || filter.trim() ? every : options;
  const shown = withSearch ? filterOptions(listed, filter) : listed;
  const moreAt = extra.length > 0 && listed === options ? shown.length : -1;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setFilter("");
    const inMore = extra.some((o) => o.value === focusValue);
    setExpanded(inMore);
    const at = (inMore ? every : options).findIndex(
      (o) => o.value === focusValue,
    );
    setHi(Math.max(at, 0));
    if (withSearch) filterRef.current?.focus();
    else listRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-idx="${hi}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [hi, open]);

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const pick = (option: SelectOption<T> | undefined) => {
    if (!option || option.disabled) return;
    onPick(option.value);
    if (closeOnPick) close();
  };

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const down = e.key === "ArrowDown";
      setHi((h) =>
        down && moreAt >= 0 && h >= shown.length - 1
          ? moreAt
          : nextIndex(shown, h, down ? 1 : -1),
      );
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setHi(e.key === "Home" ? 0 : moreAt >= 0 ? moreAt : shown.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (hi === moreAt) setExpanded(true);
      else pick(shown[hi]);
    } else if (e.key === "Escape") {
      // A dialog around the select would close on this Escape too.
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      setOpen(false);
    } else if (!withSearch && e.key.length === 1) {
      const at = typeaheadIndex(shown, hi, e.key);
      if (at >= 0) setHi(at);
    }
  };

  return (
    <div ref={rootRef} className={`select ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        className="flex h-8 w-full min-w-28 items-center gap-1.5 rounded-lg border border-input bg-transparent pr-2 pl-2.5 text-left text-sm transition-colors outline-none hover:border-ring/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-expanded:border-ring aria-expanded:ring-3 aria-expanded:ring-ring/50 dark:bg-input/30"
        title={title}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className={`truncate ${label ? "" : "text-muted-foreground"}`}>
          {label ?? placeholder}
        </span>
        <ChevronDown size={12} className="select-chevron" />
      </button>
      {open && (
        <div className="select-menu" onKeyDown={onListKey}>
          {withSearch && (
            <input
              ref={filterRef}
              className="select-filter"
              placeholder="Filter…"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setHi(0);
              }}
            />
          )}
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-multiselectable={multiselectable || undefined}
            tabIndex={-1}
            className="select-list"
            aria-activedescendant={`${listId}-${hi}`}
          >
            {shown.map((option, i) => (
              <li
                key={String(option.value)}
                id={`${listId}-${i}`}
                data-idx={i}
                role="option"
                aria-selected={isSelected(option.value)}
                aria-disabled={option.disabled}
                className={`select-option ${i === hi ? "active" : ""}`}
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(option)}
              >
                <SelectCheck on={isSelected(option.value)} />
                <span className="truncate">{option.label}</span>
                {option.hint != null && (
                  <span className="select-hint">{option.hint}</span>
                )}
              </li>
            ))}
            {moreAt >= 0 && (
              <li
                id={`${listId}-${moreAt}`}
                data-idx={moreAt}
                role="option"
                aria-selected={false}
                className={`select-option select-more ${hi === moreAt ? "active" : ""}`}
                onMouseEnter={() => setHi(moreAt)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => setExpanded(true)}
              >
                <SelectCheck on={false} />
                <span className="truncate">{more?.label}</span>
              </li>
            )}
            {shown.length === 0 && moreAt < 0 && (
              <li className="select-empty">No matches</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function SelectCheck({ on }: { on: boolean }) {
  return (
    <Check
      size={11}
      strokeWidth={2.5}
      className={`select-check ${on ? "" : "invisible"}`}
    />
  );
}
