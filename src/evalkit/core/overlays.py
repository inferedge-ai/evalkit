from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, Field

from evalkit.contracts.cases import Overlay
from evalkit.core.lint import Finding
from evalkit.store.base import IncomingOverlay


class OverlayBlock(BaseModel):
    source: str
    agents: list[str]
    tags: list[str] = Field(default_factory=list)
    evaluators: dict = Field(default_factory=dict)
    source_file: str | None = None


def overlay_evaluators(overlay: Overlay) -> dict:
    families = {
        "custom": overlay.evaluators.custom,
        "trajectory": overlay.evaluators.trajectory.as_metadata(),
    }
    return {family: block for family, block in families.items() if block}


def incoming_overlay(overlay: Overlay, source_file: str | None) -> IncomingOverlay:
    return IncomingOverlay(
        target_case_id=overlay.extends,
        agents=list(overlay.agents),
        tags=list(overlay.tags),
        evaluators=overlay_evaluators(overlay),
        source_file=source_file,
    )


def apply_overlay_blocks(evaluators: dict, blocks: list[OverlayBlock], agent: str | None) -> dict:
    out = {family: dict(block) for family, block in evaluators.items()}
    if not agent:
        return out
    for block in sorted(blocks, key=lambda b: b.source):
        if agent.partition("@")[0] not in block.agents:
            continue
        for family, named in block.evaluators.items():
            out[family] = {**out.get(family, {}), **named}
    return out


def _names(evaluators: dict) -> set[tuple[str, str]]:
    return {(family, name) for family, block in evaluators.items() for name in block}


def overlay_clashes(
    overlays: list[Overlay], existing: Mapping[str, list[OverlayBlock]]
) -> list[Finding]:
    findings: list[Finding] = []
    for overlay in overlays:
        ours = _names(overlay_evaluators(overlay))
        for block in sorted(existing.get(overlay.extends, []), key=lambda b: b.source):
            agents = sorted(set(overlay.agents) & set(block.agents))
            names = sorted(ours & _names(block.evaluators))
            if agents and names:
                findings.append(
                    Finding(
                        severity="error",
                        code="E-OVERLAY-CLASH",
                        case_id=overlay.extends,
                        message=(
                            f"{block.source} already sets "
                            f"{', '.join(f'{family}.{name}' for family, name in names)} "
                            f"for agents {', '.join(agents)}"
                        ),
                    )
                )
    return findings
