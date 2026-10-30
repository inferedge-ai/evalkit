import hashlib
import time

from tests.flows.helpers_01_06 import (
    RUBRICS,
    api,
    cases_yaml,
    cli,
    experiment_id,
    live,
    stub_judges_module,
)

__all__ = ["cli", "live", "stub_judges_module"]


def shared_dataset(cli, name: str, *case_ids: str, extra: str = "") -> None:
    cli.as_user("admin")
    cli.suite(name, {"a.yaml": cases_yaml(*case_ids) + extra})
    cli.ok("sync", name, "--workspace", "dev", "--version", "abc123", "--branch", "main")


def wait(client, exp_id: int) -> dict:
    for _ in range(500):
        body = client.get(f"/api/experiments/{exp_id}").json()
        if body["status"] in ("completed", "failed") and body["lease_holder"] is None:
            return body
        time.sleep(0.02)
    raise AssertionError(f"experiment {exp_id} stuck in {body['status']}")


def test_flow_06_shared_run_lives_in_the_shared_workspace(cli):
    shared_dataset(cli, "sv6", "sv6-1", "sv6-2")
    cli.as_user("alice")
    out = cli.ok(
        "run",
        "sv6@dev",
        "--agent",
        "ouragent@dev",
        "--runs",
        "2",
        "--case-id",
        "sv6-1",
    )
    assert "completed (1/1 ran, 1/1 evaluated)" in out
    exp_id = experiment_id(out)
    with api(cli.live, "alice") as alice:
        exp = alice.get(f"/api/experiments/{exp_id}").json()
        assert isinstance(exp["id"], int)
        assert (exp["workspace"], exp["dataset"], exp["agent"]) == (
            "dev",
            "sv6@dev",
            "ouragent@dev",
        )
        assert exp["status"] == "completed" and exp["repetitions"] == 2
        assert exp["created_by"] == "alice"
        assert exp["config"]["judge"]["base_url"] == "http://judge.test"
        assert exp["config"]["judge"]["model"] == "judge-model"
        assert exp["config"]["model"] == "fake/model-1"
        assert exp["config"]["revision"]["sha"] == "abc123"
        assert [c["case_id"] for c in alice.get(f"/api/experiments/{exp_id}/cases").json()] == [
            "sv6-1"
        ]
        [row] = [
            e for e in alice.get("/api/datasets/sv6@dev/experiments").json() if e["id"] == exp_id
        ]
        assert row["progress"] == {"total": 1, "ran": 1, "evaluated": 1}
        assert len(alice.get(f"/api/experiments/{exp_id}/runs").json()) == 2
        ui = alice.post(
            "/api/experiments",
            json={"dataset": "sv6@dev", "agent": "ouragent@dev"},
        ).json()
        assert ui["config"]["revision"]["sha"] == "abc123"
        assert ui["config"]["revision"]["branch"] == "main"
        wait(alice, ui["id"])
        missing = alice.get("/api/experiments/999999")
        assert missing.status_code == 404


def test_flow_06_only_members_start_experiments_in_a_shared_workspace(cli):
    shared_dataset(cli, "mb6", "mb6-1")
    body = {"dataset": "mb6@dev", "agent": "ouragent@dev"}
    with api(cli.live, "akshat") as akshat:
        refused = akshat.post("/api/experiments", json=body)
    assert refused.status_code == 403
    assert "only its writers, reviewers and admins" in refused.json()["detail"]
    with api(cli.live, "ravi") as ravi:
        started = ravi.post("/api/experiments", json=body)
        assert started.status_code == 200, started.text
        wait(ravi, started.json()["id"])


def test_flow_06_personal_dataset_with_a_shared_agent_lives_with_the_runner(cli):
    cli.as_user("akshat")
    cli.suite("own6", {"a.yaml": cases_yaml("own6-1")})
    out = cli.ok("run", "own6", "--agent", "ouragent@dev")
    assert "synced own6@akshat" in out
    exp_id = experiment_id(out)
    with api(cli.live, "akshat") as akshat:
        exp = akshat.get(f"/api/experiments/{exp_id}").json()
    assert (exp["workspace"], exp["dataset"], exp["status"]) == (
        "akshat",
        "own6@akshat",
        "completed",
    )
    with api(cli.live, "admin") as admin:
        denied = admin.post(f"/api/experiments/{exp_id}/evaluate")
        assert denied.status_code == 403


def test_flow_06_model_override_is_passed_only_when_accepted(cli):
    shared_dataset(cli, "md6", "md6-1")
    cli.as_user("alice")
    exp_id = experiment_id(cli.ok("run", "md6@dev", "--agent", "ouragent@dev", "--model", "gpt-x"))
    with api(cli.live, "alice") as alice:
        assert alice.get(f"/api/experiments/{exp_id}").json()["config"]["model"] == "gpt-x"
    accepts = cli.live.fake.capabilities["accepts"]
    accepts["model_override"] = False
    try:
        exp_id = experiment_id(
            cli.ok("run", "md6@dev", "--agent", "ouragent@dev", "--model", "gpt-y")
        )
    finally:
        accepts["model_override"] = True
    with api(cli.live, "alice") as alice:
        assert alice.get(f"/api/experiments/{exp_id}").json()["config"]["model"] == "fake/model-1"


def test_flow_06_fixture_cases_are_dropped_when_the_harness_takes_no_files(cli):
    data = b"fixture for flow 06"
    digest = hashlib.sha256(data).hexdigest()
    with api(cli.live, "admin") as admin:
        admin.put(f"/api/fixtures/sha256/{digest}", content=data)
    fixture_case = (
        f"  - id: fx6-2\n    fixtures: [doc6.csv]\n"
        f"    input: {{question: 'Read the file as of 2024-01-01'}}\n{RUBRICS}"
    )
    cli.as_user("admin")
    cli.suite(
        "fx6",
        {"a.yaml": cases_yaml("fx6-1") + fixture_case},
        f"fixtures:\n- key: doc6.csv\n  sha256: '{digest}'\n  context: ctx\n",
    )
    cli.ok("sync", "fx6", "--workspace", "dev")
    accepts = cli.live.fake.capabilities["accepts"]
    accepts["files"] = False
    try:
        with api(cli.live, "alice") as alice:
            created = alice.post(
                "/api/experiments",
                json={"dataset": "fx6@dev", "agent": "ouragent@dev"},
            )
            assert created.status_code == 200, created.text
            assert created.json()["dropped_cases"] == ["fx6-2"]
            exp = wait(alice, created.json()["id"])
            cases = alice.get(f"/api/experiments/{exp['id']}/cases").json()
            only_files = alice.post(
                "/api/experiments",
                json={
                    "dataset": "fx6@dev",
                    "agent": "ouragent@dev",
                    "selection": {"case_ids": ["fx6-2"]},
                },
            )
        cli.as_user("alice")
        out = cli.ok("run", "fx6@dev", "--agent", "ouragent@dev")
    finally:
        accepts["files"] = True
    assert [c["case_id"] for c in cases] == ["fx6-1"]
    assert exp["status"] == "completed"
    assert only_files.status_code == 400
    assert "every selected case needs fixture files" in only_files.json()["detail"]
    assert "skipped 1 case(s) with fixture files" in out and "fx6-2" in out


def test_flow_06_start_errors(cli):
    shared_dataset(cli, "er6", "er6-1")
    fake = cli.live.fake
    with api(cli.live, "alice") as alice:
        body = {"dataset": "er6@dev", "agent": "ouragent@dev"}
        caps = alice.post("/api/experiments", json={**body, "capabilities": fake.capabilities})
        assert caps.status_code == 400
        assert "capabilities are fetched by the server" in caps.json()["detail"]
        unknown = alice.post("/api/experiments", json={**body, "agent": "nobody@dev"})
        assert unknown.status_code == 404

        fake.capabilities["input_schema"] = {
            "type": "object",
            "required": ["ticker"],
            "properties": {"ticker": {"type": "string"}},
        }
        try:
            schema = alice.post("/api/experiments", json=body)
        finally:
            del fake.capabilities["input_schema"]
        assert schema.status_code == 400 and "er6-1" in schema.json()["detail"]

        fake.api_key = "rotated"
        try:
            down = alice.post("/api/experiments", json=body)
        finally:
            fake.api_key = "client-key"
        assert down.status_code == 502
        assert down.json()["detail"].startswith("could not start experiment")

        no_judge = alice.post(
            "/api/experiments",
            json={**body, "judge": {"base_url": "http://unknown-judge", "model": "j"}},
        )
        assert no_judge.status_code == 400
        assert "http://unknown-judge is not a registered LLM provider" in no_judge.json()["detail"]
        zero = alice.post(
            "/api/experiments",
            json={
                **body,
                "judge": {"base_url": "http://judge.test", "model": "j", "concurrency": 0},
            },
        )
        assert zero.status_code == 422


def test_flow_06_resume_reruns_only_missing_runs_and_reevaluate_keeps_scores(cli):
    shared_dataset(cli, "rs6", "rs6-1", "rs6-2")
    fake = cli.live.fake
    fake.fail_run = True
    try:
        cli.as_user("alice")
        exp_id = experiment_id(cli.ok("run", "rs6@dev", "--agent", "ouragent@dev"))
    finally:
        fake.fail_run = False
    with api(cli.live, "ravi") as ravi:
        assert ravi.post(f"/api/experiments/{exp_id}/resume").status_code == 403
    with api(cli.live, "alice") as alice:
        finished = alice.get(f"/api/experiments/{exp_id}").json()
        assert finished["status"] == "completed"
        assert finished["progress"] == {"total": 2, "ran": 2, "evaluated": 2}
        first = alice.get(f"/api/experiments/{exp_id}/runs").json()
        assert all(r["agent_run"]["status"] == "failed" for r in first)
        before = {r["agent_run_id"] for r in first}
        failed_harness_ids = {r["agent_run"]["harness_run_id"] for r in first}
        resumed = alice.post(f"/api/experiments/{exp_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "running" and resumed.json()["status_error"] is None
        done = wait(alice, exp_id)
        assert done["status"] == "completed"
        runs = alice.get(f"/api/experiments/{exp_id}/runs").json()
        assert len(runs) == 2
        assert all(r["agent_run"]["status"] == "completed" for r in runs)
        assert not before & {r["agent_run_id"] for r in runs}
        history = alice.get("/api/agents/ouragent@dev/runs", params={"limit": 200}).json()
        assert sum(r["experiment_id"] == exp_id for r in history["runs"]) == 2
        assert failed_harness_ids <= {r["run_id"] for r in history["runs"]}

        nothing_left = alice.post(f"/api/experiments/{exp_id}/resume")
        assert nothing_left.status_code == 409
        assert "no runs left to resume" in nothing_left.json()["detail"]
        scored = {
            r["experiment_run_id"]: {e["name"] for e in r["evaluations"] if e["error"] is None}
            for r in runs
        }
        assert alice.post(f"/api/experiments/{exp_id}/evaluate").status_code == 200
        assert wait(alice, exp_id)["status"] == "completed"
        again = alice.get(f"/api/experiments/{exp_id}/runs").json()
    for run in again:
        names = {e["name"] for e in run["evaluations"] if e["error"] is None}
        assert scored[run["experiment_run_id"]] <= names


def test_flow_06_reevaluate_during_a_live_run_on_the_same_pod_conflicts(cli):
    shared_dataset(cli, "lv6", "lv6-1")
    fake = cli.live.fake
    fake.complete_after_polls = 10**9
    try:
        with api(cli.live, "alice") as alice:
            created = alice.post(
                "/api/experiments",
                json={"dataset": "lv6@dev", "agent": "ouragent@dev"},
            ).json()
            evaluate = alice.post(f"/api/experiments/{created['id']}/evaluate")
            resume = alice.post(f"/api/experiments/{created['id']}/resume")
    finally:
        fake.complete_after_polls = 0
    assert evaluate.status_code == 409 and resume.status_code == 409
    with api(cli.live, "alice") as alice:
        assert wait(alice, created["id"])["status"] == "completed"


def test_flow_06_experiments_pin_case_versions(cli):
    shared_dataset(cli, "pn6", "pn6-1")
    cli.as_user("alice")
    exp_id = experiment_id(cli.ok("run", "pn6@dev", "--agent", "ouragent@dev"))
    with api(cli.live, "alice") as alice:
        [pinned] = alice.get(f"/api/experiments/{exp_id}/cases").json()
    cli.as_user("admin")
    (cli.tmp_path / "suites" / "pn6" / "a.yaml").write_text(
        cases_yaml("pn6-1", question="Edited 2024-01-01?")
    )
    cli.ok("sync", "pn6", "--workspace", "dev")
    with api(cli.live, "alice") as alice:
        [now] = alice.get("/api/datasets/pn6@dev/cases").json()
        [still] = alice.get(f"/api/experiments/{exp_id}/cases").json()
    assert now["content_hash"] != pinned["content_hash"]
    assert still["content_hash"] == pinned["content_hash"]
