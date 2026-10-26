import json

import pytest

from tests.api.test_otel import PROTO, otlp_payload
from tests.fake_agents import reference_mapping
from tests.flows.helpers_12_17 import DEV, live_stack

AGENT = f"deployed@{DEV}"
DRAFT = {
    "schema_version": "1",
    "kind": {"attribute": "openinference.span.kind", "values": {"LLM": "llm", "CHAIN": "chain"}},
    "model": {"attribute": "llm.model_name"},
    "exclude_spans": ["llm"],
}


@pytest.fixture
def live(db_url, tmp_path, monkeypatch, stub_judges):
    with live_stack(db_url, tmp_path, monkeypatch) as stack:
        admin = stack.http()
        admin.post(
            "/api/agents",
            json={
                "workspace": DEV,
                "name": "deployed",
                "trajectory_source": "otel",
                "mapping": reference_mapping(),
            },
        ).raise_for_status()
        admin.post(
            "/api/otel/v1/traces",
            content=otlp_payload("s-1"),
            headers={**PROTO, "X-Evalkit-Agent": AGENT},
        ).raise_for_status()
        yield stack


def check_lines(output: str) -> dict[str, tuple[str, str]]:
    found = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2 and parts[0] in ("✓", "!", "✗"):
            found[parts[1]] = (parts[0], parts[2] if len(parts) > 2 else "")
    return found


def test_flow_13_traces_check_prints_every_line_and_dumps(live, tmp_path):
    result = live.run("traces", "check", AGENT, "s-1")
    assert result.exit_code == 0, result.output
    lines = check_lines(result.output)
    assert set(lines) == {"session", "root", "mapping", "llm", "tools", "unmapped", "trajectory"}
    assert lines["session"][0] == "✓" and "2 spans in 1 trace" in lines["session"][1]
    assert "settled" in lines["session"][1]
    assert lines["mapping"] == ("✓", "v1")
    assert "model m" in lines["llm"][1]
    for name, key in (("spans", "name"), ("esf", "kind"), ("trajectory", "steps")):
        target = tmp_path / "out" / name / f"run-{name}.json"
        dumped = live.run("traces", "check", AGENT, "s-1", "--dump", str(target))
        assert dumped.exit_code == 0, dumped.output
        payload = json.loads(target.read_text())
        assert key in (payload[0] if isinstance(payload, list) else payload)
    wrong = live.run("traces", "check", AGENT, "s-1", "--dump", str(tmp_path / "x.txt"))
    assert wrong.exit_code == 1 and "must end in one of" in wrong.output
    missing = live.run("traces", "check", AGENT, "no-such-session")
    assert missing.exit_code == 1 and check_lines(missing.output)["root"][0] == "✗"
    unknown = live.run("traces", "check", f"ghost@{DEV}", "s-1")
    assert unknown.exit_code == 1 and "unknown agent" in unknown.output


def test_flow_13_mapping_push_list_activate(live, tmp_path):
    no_draft = live.run("traces", "check", AGENT, "s-1", "--mapping", "draft")
    assert no_draft.exit_code == 1 and "no draft mapping" in no_draft.output
    document = tmp_path / "draft.json"
    document.write_text(json.dumps(DRAFT))
    pushed = live.run("traces", "mapping", "push", AGENT, str(document))
    assert pushed.exit_code == 0 and "draft v2" in pushed.output
    preview = live.run("traces", "check", AGENT, "s-1", "--mapping", "draft")
    assert check_lines(preview.output)["mapping"][1] == "v2"
    again = live.run("traces", "mapping", "push", AGENT, str(document))
    assert "draft v3" in again.output
    rows = [
        line.split() for line in live.run("traces", "mapping", "list", AGENT).output.splitlines()
    ]
    assert [(r[0], r[1]) for r in rows] == [("v1", "active"), ("v2", "retired"), ("v3", "draft")]
    assert all(r[2:4] == ["by", "admin"] for r in rows[1:])
    activated = live.run("traces", "mapping", "push", AGENT, str(document), "--activate")
    assert "active v4" in activated.output
    statuses = {
        r["version"]: r["status"] for r in live.http().get(f"/api/agents/{AGENT}/mappings").json()
    }
    assert statuses == {1: "retired", 2: "retired", 3: "retired", 4: "active"}
    after = live.http().get(f"/api/agents/{AGENT}/sessions/s-1/trajectory").json()
    assert after["mapping_version"] == 4
    assert live.http().post(f"/api/agents/{AGENT}/mappings/9/activate").status_code == 404
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1", "kind": {"attr": "x"}}))
    refused = live.run("traces", "mapping", "push", AGENT, str(bad))
    assert refused.exit_code == 1 and "attribute" in refused.output


def test_flow_13_mapping_writes_follow_workspace_writes(live):
    alice, admin = live.http("alice"), live.http()
    assert alice.put(f"/api/agents/{AGENT}/mappings/draft", json=DRAFT).status_code == 403
    assert alice.get(f"/api/agents/{AGENT}/mappings").status_code == 200
    live.run("workspaces", "members", "add", DEV, "alice", "--role", "writer")
    assert alice.put(f"/api/agents/{AGENT}/mappings/draft", json=DRAFT).status_code == 200
    akshat = live.http("akshat")
    akshat.post(
        "/api/agents",
        json={
            "workspace": "akshat",
            "name": "traced",
            "trajectory_source": "otel",
            "mapping": reference_mapping(),
        },
    ).raise_for_status()
    assert admin.put("/api/agents/traced@akshat/mappings/draft", json=DRAFT).status_code == 403
    assert akshat.put("/api/agents/traced@akshat/mappings/draft", json=DRAFT).status_code == 200
    assert admin.get("/api/trace-mappings/builtin").status_code == 404
