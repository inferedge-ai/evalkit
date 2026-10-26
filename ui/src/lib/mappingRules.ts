export interface AttributeRule {
  attribute: string;
  json?: boolean;
  values?: Record<string, string>;
  message_format?: string;
}

export interface MappingRuleRow {
  field: string;
  label: string;
  rule: AttributeRule | null;
}

const FIELDS: [string, string][] = [
  ["kind", "Span kind"],
  ["session_id", "Session id"],
  ["model", "Model"],
  ["input_messages", "Input messages"],
  ["output_messages", "Output messages"],
  ["tokens.prompt", "Prompt tokens"],
  ["tokens.completion", "Completion tokens"],
  ["tokens.cached", "Cached tokens"],
  ["cost_usd", "Cost (USD)"],
  ["tool.name", "Tool name"],
  ["tool.call_id", "Tool call id"],
  ["tool.arguments", "Tool arguments"],
  ["tool.result", "Tool result"],
  ["input", "Span input"],
  ["output", "Span output"],
];

function lookup(doc: Record<string, unknown>, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, key) =>
        node && typeof node === "object"
          ? (node as Record<string, unknown>)[key]
          : undefined,
      doc,
    );
}

function asRule(value: unknown): AttributeRule | null {
  return value && typeof value === "object" && "attribute" in value
    ? (value as AttributeRule)
    : null;
}

export function mappingRows(doc: Record<string, unknown>): MappingRuleRow[] {
  return FIELDS.map(([field, label]) => ({
    field,
    label,
    rule: asRule(lookup(doc, field)),
  }));
}

function ruleKey(rule: AttributeRule | null): string {
  if (!rule) return "";
  const values = Object.entries(rule.values ?? {}).sort(([x], [y]) =>
    x.localeCompare(y),
  );
  return JSON.stringify([
    rule.attribute,
    Boolean(rule.json),
    rule.message_format ?? null,
    values,
  ]);
}

export function sameRule(a: AttributeRule | null, b: AttributeRule | null) {
  return ruleKey(a) === ruleKey(b);
}

export function ruleNotes(rule: AttributeRule): string[] {
  const notes: string[] = [];
  if (rule.message_format) notes.push(`${rule.message_format} messages`);
  if (rule.json) notes.push("parsed as JSON");
  return notes;
}
