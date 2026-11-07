from typing import Any

import pytest

from evalkit.contracts.enums import ExperimentStatus, ScoredOn
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import explicit
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS
from evalkit.core.evaluators.rubrics import ANSWER_RUBRIC_EMITS
from evalkit.core.judge import JudgeUsage
from evalkit.core.runner import (
    ExperimentRequest,
    NoJudge,
    evaluate_experiment,
    harness_capabilities,
    prepare_experiment,
)
from evalkit.core.server_evaluators import Location, ServerScore, resolve_location
from evalkit.core.workspace import parse_slug
from evalkit.store.base import ScoreRow
from tests.fake_agents import JUDGE_URL
from tests.native import HOLDER, seed_env, start, suite_case, sync_cases

PROVIDER = JudgeSettings(provider="judge", model="judge-model")
BYOK = JudgeSettings(base_url=JUDGE_URL, model="judge-model")
RUN = ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")


class FakeServer:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[int, str, dict]] = []
        self.fail = fail

    async def score(self, experiment_run_id: int, name: str, config: dict[str, Any]) -> ServerScore:
        self.calls.append((experiment_run_id, name, config))
        if self.fail:
            raise ConnectionError("server down")
        return ServerScore(
            rows=[ScoreRow(name=emit, score=0.9, label="pass") for emit in ANSWER_RUBRIC_EMITS],
            judge_usage=JudgeUsage(input_tokens=1000, output_tokens=100),
        )


@pytest.mark.parametrize(
    ("name", "judge", "where"),
    [
        ("answer_rubrics", PROVIDER, Location.SERVER),
        ("answer_rubrics", BYOK, Location.LOCAL),
        ("presentation_judge", PROVIDER, Location.SERVER),
        ("presentation_judge", BYOK, Location.LOCAL),
        ("trajectory_rubrics", PROVIDER, Location.SERVER),
        ("trajectory_rubrics", BYOK, Location.LOCAL),
        ("tool_trajectory_match", PROVIDER, Location.LOCAL),
        ("required_tools_called", PROVIDER, Location.LOCAL),
        ("max_response_time", PROVIDER, Location.LOCAL),
        ("max_response_time", BYOK, Location.LOCAL),
    ],
)
def test_resolve_location(name, judge, where):
    assert resolve_location(BUILTIN_EVALUATORS[name], judge) is where


async def _scored(env, server: FakeServer, judge: JudgeSettings):
    env.deps.server_evaluators = server
    exp = await start(env, RUN.model_copy(update={"judge": judge}))
    assert exp.status is ExperimentStatus.COMPLETED, exp.status_error
    return exp, {e.name: e for e in (await env.details(exp.id))[0].evaluations}


async def test_provider_judge_scores_judge_builtins_through_the_server(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path)
    server = FakeServer()
    exp, rows = await _scored(env, server, PROVIDER)
    assert {name for _, name, _ in server.calls} == {"answer_rubrics"}
    assert server.calls[0][2]["rubrics"][0]["rubric"] == "States revenue of $1.2B."
    assert rows["answer_rubric_score"].value == 0.9
    assert rows["answer_rubric_score"].scored_on is ScoredOn.SERVER
    assert rows["max_response_time"].scored_on is ScoredOn.LOCAL
    assert rows["required_tool_calls"].scored_on is ScoredOn.LOCAL
    assert rows["gauge_judge_cost_usd"].value == pytest.approx(0.0012)

    server.calls.clear()
    await evaluate_experiment(env.deps, exp.id)
    assert server.calls == []


async def test_base_url_judge_never_calls_the_server(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    server = FakeServer()
    _, rows = await _scored(env, server, BYOK)
    assert server.calls == []
    assert rows["answer_rubric_score"].value == 0.5
    assert rows["answer_rubric_score"].scored_on is ScoredOn.LOCAL


async def test_a_failing_server_call_is_an_error_row_and_rescored_later(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path)
    server = FakeServer(fail=True)
    exp, rows = await _scored(env, server, PROVIDER)
    assert rows["answer_rubrics"].label == "error"
    assert "server down" in (rows["answer_rubrics"].error or "")
    assert rows["answer_rubrics"].scored_on is ScoredOn.SERVER
    assert rows["max_response_time"].error is None
    assert len(server.calls) == 2
    server.fail = False
    await evaluate_experiment(env.deps, exp.id)
    assert len(server.calls) == 4
    rows = {e.name: e for e in (await env.details(exp.id))[0].evaluations}
    assert rows["answer_rubric_score"].value == 0.9


async def test_server_runs_stamp_every_row_server(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    env.deps.scored_on = ScoredOn.SERVER
    exp = await start(env, RUN.model_copy(update={"judge": BYOK}))
    assert {e.scored_on for e in (await env.details(exp.id))[0].evaluations} == {ScoredOn.SERVER}


async def test_no_judge_is_refused_only_when_a_case_needs_one(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path)
    agent = await env.deps.agents.get(explicit(parse_slug("fake@canonical")))
    caps = await harness_capabilities(
        agent, env.deps.credentials.key_for("agent", agent.ref), env.deps.harness_transport
    )

    async def prepare_without_judge(dataset: str):
        return await prepare_experiment(
            env.store,
            ExperimentRequest(dataset=dataset, agent="fake@canonical"),
            agent=agent,
            caps=caps,
            remote_evaluators={},
            judge=None,
            runner="admin",
            holder=HOLDER,
        )

    with pytest.raises(NoJudge):
        await prepare_without_judge("finance@canonical")
    await sync_cases(
        env,
        [
            suite_case(
                "plain-1", "q1", evaluators={"output": {"max_response_time": {"max_seconds": 600}}}
            )
        ],
        dataset="plain",
    )
    exp, _ = await prepare_without_judge("plain@canonical")
    assert exp.config.judge is None
