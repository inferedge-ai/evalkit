import re
from pathlib import Path

import pytest

from evalkit.client import links

APP_ROUTES = Path(__file__).parents[2] / "ui" / "src" / "app.tsx"
SERVER = "https://evalkit.example/"


def _route_patterns() -> dict[str, re.Pattern[str]]:
    declared = re.findall(r'path="(/[^"]*)"', APP_ROUTES.read_text())
    return {
        route: re.compile(re.sub(r":[A-Za-z]+", "[^/]+", route) + "$")
        for route in declared
        if route != "*"
    }


@pytest.mark.parametrize(
    ("url", "route", "path"),
    [
        (
            links.build_experiment_url(SERVER, 612),
            "/experiments/:experimentId",
            "/experiments/612",
        ),
        (
            links.build_eval_run_url(SERVER, 612, "q 1/2"),
            "/experiments/:experimentId/:exampleId",
            "/experiments/612/q%201%2F2",
        ),
        (
            links.build_dataset_url(SERVER, "finance@dev"),
            "/datasets/:ref",
            "/datasets/finance%40dev",
        ),
        (
            links.build_case_url(SERVER, "finance@dev", "mf-07"),
            "/datasets/:ref/cases/:caseId",
            "/datasets/finance%40dev/cases/mf-07",
        ),
        (
            links.build_agent_url(SERVER, "ouragent@dev"),
            "/agents/:agent",
            "/agents/ouragent%40dev",
        ),
        (
            links.build_agent_run_url(SERVER, "ouragent@dev", "run-1"),
            "/runs/:agent/:runId",
            "/runs/ouragent%40dev/run-1",
        ),
    ],
)
def test_each_link_matches_the_route_the_ui_declares(url, route, path):
    patterns = _route_patterns()
    assert url == f"https://evalkit.example{path}"
    assert patterns[route].match(path)
    assert [r for r, pattern in patterns.items() if pattern.match(path)] == [route]


def test_segments_are_encoded_like_encode_uri_component():
    assert links.build_case_url(SERVER, "a@b", "x(1)!*'~") == (
        "https://evalkit.example/datasets/a%40b/cases/x(1)!*'~"
    )
