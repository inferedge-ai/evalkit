import json
from importlib import resources
from pathlib import Path

import httpx
from pydantic import BaseModel

from evalkit.config import CostMapConfig

# TODO: per-client pricing (negotiated rates, private models)

COST_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)


class Rates(BaseModel):
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    cache_read_input_token_cost: float | None = None


def bundled_cost_map_path() -> Path:
    return Path(str(resources.files("evalkit.core"))) / "data" / "cost_map.json"


def fetch_cost_map() -> dict:
    resp = httpx.get(COST_MAP_URL, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def write_cost_map(cost_map: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(cost_map, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def load_cost_map(cfg: CostMapConfig) -> dict:
    if cfg.source == "bundled":
        path = bundled_cost_map_path()
        if path.is_file():
            return json.loads(path.read_text())
        cost_map = fetch_cost_map()
        write_cost_map(cost_map, path)
        return cost_map
    if cfg.source.startswith(("http://", "https://")):
        resp = httpx.get(cfg.source, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    return json.loads(Path(cfg.source).read_text())


def resolve_rates(cost_map: dict, model: str) -> Rates | None:
    if not model:
        return None
    parts = model.split("/")
    candidates = ["/".join(parts[i:]) for i in range(len(parts))]
    bare = parts[-1]
    candidates += [f"vertex_ai/{bare}", f"gemini/{bare}", f"azure/{bare}"]
    for c in candidates:
        if c in cost_map:
            entry = cost_map[c]
            return Rates(
                input_cost_per_token=entry.get("input_cost_per_token") or 0.0,
                output_cost_per_token=entry.get("output_cost_per_token") or 0.0,
                cache_read_input_token_cost=entry.get("cache_read_input_token_cost"),
            )
    return None


def cost_usd(
    rates: Rates,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
) -> float:
    read_rate = (
        rates.cache_read_input_token_cost
        if rates.cache_read_input_token_cost is not None
        else rates.input_cost_per_token
    )
    remaining = max(input_tokens - cached_tokens, 0)
    return round(
        remaining * rates.input_cost_per_token
        + output_tokens * rates.output_cost_per_token
        + cached_tokens * read_rate,
        10,
    )
