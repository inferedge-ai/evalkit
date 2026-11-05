export interface ApiKeyValue {
  key: string;
  clear: boolean;
}

export function ApiKeyField({
  hasKey,
  value,
  onChange,
}: {
  hasKey: boolean;
  value: ApiKeyValue;
  onChange: (value: ApiKeyValue) => void;
}) {
  if (hasKey && !value.clear && !value.key)
    return (
      <div className="flex items-center gap-2">
        <span className="chip muted">Key set</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChange({ key: "", clear: true })}
        >
          Replace or clear
        </button>
      </div>
    );
  return (
    <input
      className="input min-w-0 flex-1"
      type="password"
      aria-label="API key"
      placeholder={
        hasKey ? "new key, or leave empty to remove it" : "API key (optional)"
      }
      autoComplete="off"
      value={value.key}
      onChange={(e) => onChange({ key: e.target.value, clear: value.clear })}
    />
  );
}
