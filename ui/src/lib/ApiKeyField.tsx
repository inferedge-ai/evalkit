import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
export interface ApiKeyValue {
  key: string;
  clear: boolean;
}

export function ApiKeyField({
  id,
  hasKey,
  value,
  onChange,
}: {
  id?: string;
  hasKey: boolean;
  value: ApiKeyValue;
  onChange: (value: ApiKeyValue) => void;
}) {
  if (hasKey && !value.clear && !value.key)
    return (
      <div className="flex items-center gap-2">
        <Badge variant="secondary">Key set</Badge>
        <Button
          id={id}
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange({ key: "", clear: true })}
        >
          Replace or clear
        </Button>
      </div>
    );
  return (
    <Input
      id={id}
      className="min-w-0 flex-1"
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
