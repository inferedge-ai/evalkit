// A saved order lists the columns the user arranged; columns added since (a
// new bucket, a new feature) slot in after the column they follow by default,
// and columns that no longer exist drop out.
export function mergeOrder(saved: string[], ids: string[]): string[] {
  const kept = saved.filter((id) => ids.includes(id));
  for (const [i, id] of ids.entries()) {
    if (kept.includes(id)) continue;
    const before = ids
      .slice(0, i)
      .reverse()
      .find((p) => kept.includes(p));
    kept.splice(before === undefined ? 0 : kept.indexOf(before) + 1, 0, id);
  }
  return kept;
}

export function moveColumn(
  order: string[],
  from: string,
  to: string,
): string[] {
  const next = order.filter((id) => id !== from);
  const at = order.indexOf(to);
  const fromAt = order.indexOf(from);
  next.splice(fromAt < at ? next.indexOf(to) + 1 : next.indexOf(to), 0, from);
  return next;
}
