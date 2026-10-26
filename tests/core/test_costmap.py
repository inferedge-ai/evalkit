import json

import pytest

from evalkit.config import CostMapConfig
from evalkit.core.costmap import Rates, cost_usd, load_cost_map, resolve_rates

FIXTURE_MAP = {
    "vertex_ai/gemini-3.6-flash": {
        "input_cost_per_token": 1e-7,
        "output_cost_per_token": 4e-7,
        "cache_read_input_token_cost": 2.5e-8,
    }
}


def test_bundled_missing_fetches_and_caches(tmp_path, monkeypatch):
    path = tmp_path / "cost_map.json"
    monkeypatch.setattr("evalkit.core.costmap.bundled_cost_map_path", lambda: path)
    monkeypatch.setattr("evalkit.core.costmap.fetch_cost_map", lambda: FIXTURE_MAP)
    assert load_cost_map(CostMapConfig(source="bundled")) == FIXTURE_MAP
    assert json.loads(path.read_text()) == FIXTURE_MAP


def test_bundled_reads_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "cost_map.json"
    path.write_text(json.dumps(FIXTURE_MAP))
    monkeypatch.setattr("evalkit.core.costmap.bundled_cost_map_path", lambda: path)
    monkeypatch.setattr(
        "evalkit.core.costmap.fetch_cost_map", lambda: pytest.fail("unexpected fetch")
    )
    assert load_cost_map(CostMapConfig(source="bundled")) == FIXTURE_MAP


def test_file_source_loads(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps(FIXTURE_MAP))
    assert load_cost_map(CostMapConfig(source=str(path))) == FIXTURE_MAP


def test_resolve_bare_name_probes_prefixes():
    rates = resolve_rates(FIXTURE_MAP, "gemini-3.6-flash")
    assert rates is not None
    assert rates.input_cost_per_token == 1e-7


def test_resolve_progressive_suffix():
    rates = resolve_rates(FIXTURE_MAP, "proxy/vertex_ai/gemini-3.6-flash")
    assert rates is not None


def test_resolve_unknown():
    assert resolve_rates(FIXTURE_MAP, "unknown-model") is None
    assert resolve_rates(FIXTURE_MAP, "") is None


def test_cost_math():
    rates = Rates(
        input_cost_per_token=1e-6,
        output_cost_per_token=2e-6,
        cache_read_input_token_cost=1e-7,
    )
    total = cost_usd(rates, input_tokens=1000, output_tokens=100, cached_tokens=400)
    assert total == round(600 * 1e-6 + 100 * 2e-6 + 400 * 1e-7, 10)


def test_cost_math_no_cache_rate_falls_back_to_input_rate():
    rates = Rates(input_cost_per_token=1e-6, output_cost_per_token=0.0)
    assert cost_usd(rates, 100, 0, 40) == round(60 * 1e-6 + 40 * 1e-6, 10)
