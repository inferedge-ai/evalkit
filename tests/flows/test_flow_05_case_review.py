import re

from tests.flows.helpers_01_06 import api, cases_yaml, cli, experiment_id, live, stub_judges_module

__all__ = ["cli", "live", "stub_judges_module"]


def shared_suite(cli, name: str, *case_ids: str) -> None:
    cli.as_user("admin")
    cli.suite(name, {"a.yaml": cases_yaml(*case_ids)})
    cli.ok("sync", name, "--workspace", "dev")


def test_flow_05_review_applies_only_to_shared_suites(cli):
    cli.as_user("akshat")
    cli.suite("own5", {"a.yaml": cases_yaml("own5-1")})
    cli.ok("sync", "own5")
    with api(cli.live, "akshat") as akshat:
        [case] = akshat.get("/api/datasets/own5@akshat/cases").json()
        assert case["review"] is None
        resp = akshat.post(
            "/api/datasets/own5@akshat/review-requests",
            json={"selection": {"all": True}, "reviewer": "ravi"},
        )
        assert resp.status_code == 409
    assert re.search(r"own5-1\s+-", cli.ok("review", "show", "own5"))


def test_flow_05_request_queue_decide_and_changelog(cli):
    shared_suite(cli, "rv5", "rv5-1", "rv5-2", "rv5-3")
    cli.as_user("alice")
    bad = cli.fails("review", "request", "rv5@dev", "--case-id", "rv5-1", "--reviewer", "akshat")
    assert "akshat is not a reviewer of dev" in bad
    out = cli.ok("review", "request", "rv5@dev", "--reviewer", "ravi")
    assert "requested review from ravi on 3 case(s)" in out

    cli.as_user("ravi")
    queue = cli.ok("review", "queue")
    assert all(f"rv5-{n}" in queue for n in (1, 2, 3))
    assert "rv5-1" in cli.ok("review", "queue", "--all")

    cli.as_user("akshat")
    assert "only reviewers of this workspace decide" in cli.fails(
        "review", "approve", "rv5@dev", "rv5-1"
    )
    cli.as_user("ravi")
    assert cli.fails("review", "request-changes", "rv5@dev", "rv5-2")
    assert "rv5-1: approved" in cli.ok("review", "approve", "rv5@dev", "rv5-1", "--comment", "ok")
    cli.ok("review", "request-changes", "rv5@dev", "rv5-2", "--comment", "cite the filing")
    assert "rv5-2" in cli.ok("review", "queue", "--changes-requested")
    queue = cli.ok("review", "queue")
    assert "rv5-3" in queue and "rv5-1" not in queue and "rv5-2" not in queue

    with api(cli.live, "ravi") as ravi:
        states = {
            c["case_id"]: c["review"]["state"]
            for c in ravi.get("/api/datasets/rv5@dev/cases").json()
        }
        assert states == {
            "rv5-1": "approved",
            "rv5-2": "changes_requested",
            "rv5-3": "review_requested",
        }
        events = ravi.get("/api/datasets/rv5@dev/cases/rv5-1/events").json()["events"][::-1]
        assert [e["kind"] for e in events] == ["synced", "review_requested", "approved"]
        assert events[1]["actor"] == "alice" and events[1]["target"] == "ravi"
        assert all(e["content_hash"] == events[0]["content_hash"] for e in events)
        threads = ravi.get("/api/datasets/rv5@dev/cases/rv5-1/comments").json()["threads"]
        assert [t["body"] for t in threads] == ["ok"]

    shown = cli.ok("review", "show", "rv5@dev", "rv5-2")
    assert "state: changes_requested" in shown and "cite the filing" in shown


def test_flow_05_comments_nest_once_and_resolve_rules(cli):
    shared_suite(cli, "cm5", "cm5-1")
    path = "/api/datasets/cm5@dev/cases/cm5-1/comments"
    with (
        api(cli.live, "akshat") as akshat,
        api(cli.live, "alice") as alice,
        api(cli.live, "ravi") as ravi,
    ):
        assert akshat.post(path, json={"body": "  "}).status_code == 400
        top = akshat.post(path, json={"body": "why 42?"}).json()
        reply = alice.post(path, json={"body": "see p3", "parent_id": top["id"]}).json()
        nested = akshat.post(path, json={"body": "deeper", "parent_id": reply["id"]})
        assert (
            nested.status_code == 400 and nested.json()["detail"] == "replies nest one level only"
        )
        assert alice.patch(f"{path}/{top['id']}", json={"resolved": True}).status_code == 403
        assert akshat.patch(f"{path}/{top['id']}", json={"resolved": True}).status_code == 200
        assert ravi.patch(f"{path}/{top['id']}", json={"resolved": False}).status_code == 200
        [thread] = akshat.get(path).json()["threads"]
        assert [r["body"] for r in thread["replies"]] == ["see p3"]
        assert thread["resolved"] is False
        other = akshat.post("/api/datasets/cm5@dev/cases/nope/comments", json={"body": "x"})
        assert other.status_code == 404


def test_flow_05_reviewers_are_per_workspace(cli):
    cli.as_user("admin")
    cli.suite("pw5", {"a.yaml": cases_yaml("pw5-1")})
    cli.ok("sync", "pw5", "--workspace", "prod")
    with api(cli.live, "ravi") as ravi:
        resp = ravi.post("/api/datasets/pw5@prod/cases/pw5-1/review", json={"action": "approve"})
        assert resp.status_code == 403
        members = ravi.get("/api/workspaces/dev/members").json()
        assert any(m["username"] == "ravi" and m["role"] == "reviewer" for m in members)


def test_flow_05_runs_ignore_review_state(cli):
    shared_suite(cli, "ap5", "ap5-1", "ap5-2")
    with api(cli.live, "ravi") as ravi:
        ravi.post("/api/datasets/ap5@dev/cases/ap5-1/review", json={"action": "approve"})
    cli.as_user("alice")
    exp = experiment_id(cli.ok("run", "ap5@dev", "--agent", "ouragent@dev"))
    with api(cli.live, "alice") as alice:
        assert sorted(c["case_id"] for c in alice.get(f"/api/experiments/{exp}/cases").json()) == [
            "ap5-1",
            "ap5-2",
        ]
