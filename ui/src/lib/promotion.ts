import { fmtWhen } from "./format";
import { stateLabel } from "./review";
import type { TwinStatus, WorkspaceInfo } from "./types";

export type PromotionSide = "source" | "target";

export function promotionSide(
  ws: WorkspaceInfo | undefined,
): PromotionSide | null {
  if (!ws) return null;
  if (ws.promotes_to) return "source";
  if (ws.promoted_from) return "target";
  return null;
}

export function twinLine(twin: TwinStatus, side: PromotionSide): string {
  const ws = twin.workspace;
  if (twin.state === "absent")
    return side === "source" ? `Not in ${ws}` : `Removed in ${ws}`;
  const parts = [
    twin.state === "same"
      ? side === "source"
        ? `Promoted to ${ws}`
        : `Same version in ${ws}`
      : `${ws} has ${twin.content_hash ?? "another version"}`,
  ];
  if (twin.review) {
    const when = fmtWhen(twin.review.updated_at);
    parts.push(
      when
        ? `${stateLabel(twin.review.state).toLowerCase()} ${when}`
        : stateLabel(twin.review.state).toLowerCase(),
    );
  }
  if (twin.overlays_differ) parts.push(`overlays differ from ${ws}`);
  return parts.join(" · ");
}

export function twinCell(
  twin: TwinStatus | null | undefined,
  side: PromotionSide | null,
): { label: string; cls: string } | null {
  if (!twin || !side) return null;
  if (side === "source") {
    if (twin.state === "same") return { label: "Promoted", cls: "chip-pass" };
    if (twin.state === "differs")
      return { label: `ahead of ${twin.workspace}`, cls: "chip-info" };
    return { label: `not in ${twin.workspace}`, cls: "muted" };
  }
  if (twin.state === "same") return { label: "same", cls: "muted" };
  if (twin.state === "differs")
    return { label: `newer in ${twin.workspace}`, cls: "chip-info" };
  return { label: `removed in ${twin.workspace}`, cls: "chip-partial" };
}

export function promotedImpliesApproved(
  twin: TwinStatus | null | undefined,
  side: PromotionSide | null,
  state: string | undefined,
): boolean {
  return side === "source" && twin?.state === "same" && state === "approved";
}
