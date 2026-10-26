from tests.flows.helpers_01_06 import (
    api,
    cases_yaml,
    cli,
    live,
    stub_judges_module,
    sync_counts,
)

__all__ = ["cli", "live", "stub_judges_module"]


def case_ids(client, ref: str) -> list[str]:
    return sorted(c["case_id"] for c in client.get(f"/api/datasets/{ref}/cases").json())


def test_flow_03_sync_targets_your_workspace_and_prunes_only_whole_datasets(cli):
    cli.as_user("alice")
    root = cli.suite(
        "fin3", {"a.yaml": cases_yaml("f3-1", "f3-2"), "sub/b.yaml": cases_yaml("f3-3")}
    )
    out = cli.ok("sync", "fin3")
    assert "synced fin3@alice" in out
    assert sync_counts(out) == {"created": 3, "patched": 0, "deleted": 0, "overlays_applied": 0}
    assert sync_counts(cli.ok("sync", "fin3")) == {
        "created": 0,
        "patched": 0,
        "deleted": 0,
        "overlays_applied": 0,
    }
    with api(cli.live, "alice") as alice:
        splits = alice.get("/api/datasets/fin3@alice/splits").json()
        assert {"name": "sub", "count": 1} in splits, splits

        (root / "a.yaml").write_text(cases_yaml("f3-1", question="Changed question 2024-01-01?"))
        out = cli.ok("sync", "fin3/sub")
        assert sync_counts(out)["deleted"] == 0
        assert case_ids(alice, "fin3@alice") == ["f3-1", "f3-2", "f3-3"]
        out = cli.ok("sync", "fin3")
        counts = sync_counts(out)
        assert (counts["patched"], counts["deleted"]) == (1, 1)
        assert case_ids(alice, "fin3@alice") == ["f3-1", "f3-3"]


def test_flow_03_who_may_sync_a_shared_workspace(cli):
    cli.suite("shared3", {"a.yaml": cases_yaml("s3-1")})
    cli.as_user("akshat")
    assert "you cannot sync to @dev" in cli.fails("sync", "shared3", "--workspace", "dev")
    cli.as_user("alice")
    assert "synced shared3@dev" in cli.ok("sync", "shared3", "--workspace", "dev")
    cli.as_user("admin")
    assert "synced shared3@dev" in cli.ok("sync", "shared3", "--workspace", "dev")


def test_flow_03_sync_records_revision_and_actor(cli):
    cli.suite("rev3", {"a.yaml": cases_yaml("r3-1")})
    cli.ok(
        "sync",
        "rev3",
        "--workspace",
        "dev",
        "--version",
        "abc123",
        "--branch",
        "main",
        "--no-dirty",
        "--actor",
        "alice",
    )
    with api(cli.live, "akshat") as akshat:
        dataset = akshat.get("/api/datasets/rev3@dev").json()
    detail = dataset["last_sync"]["detail"]
    assert (detail["sha"], detail["branch"], detail["dirty"]) == ("abc123", "main", False)
    assert detail["counts"]["created"] == 1
    assert detail["actor"] == "alice"
    assert dataset["last_sync"]["actor"] == "alice"
    cli.as_user("alice")
    assert "actor override is admin only" in cli.fails(
        "sync", "rev3", "--workspace", "dev", "--actor", "ravi"
    )


def test_flow_03_shared_sync_returns_approved_cases_to_draft_and_keeps_reviews(cli):
    root = cli.suite("rv3", {"a.yaml": cases_yaml("rv3-1", "rv3-2", "rv3-3")})
    cli.ok("sync", "rv3", "--workspace", "dev")
    with api(cli.live, "ravi") as ravi, api(cli.live, "admin") as admin:
        approved = ravi.post("/api/datasets/rv3@dev/cases/rv3-1/review", json={"action": "approve"})
        assert approved.status_code == 200, approved.text
        requested = admin.post(
            "/api/datasets/rv3@dev/review-requests",
            json={"selection": {"case_ids": ["rv3-2"]}, "reviewer": "ravi"},
        )
        assert requested.status_code == 200, requested.text
        (root / "a.yaml").write_text(
            cases_yaml("rv3-1", "rv3-2", question="New wording 2024-01-01?")
            + cases_yaml("rv3-3").removeprefix("cases:\n")
        )
        out = cli.ok("sync", "rv3", "--workspace", "dev")
        assert "1 approved case(s) changed and are now draft: rv3-1" in out
        assert "1 case(s) updated and still under review: rv3-2 (ravi)" in out
        states = {
            c["case_id"]: c["review_state"] for c in ravi.get("/api/datasets/rv3@dev/cases").json()
        }
        assert states == {"rv3-1": "draft", "rv3-2": "review_requested", "rv3-3": "draft"}
        kinds = [
            e["kind"] for e in ravi.get("/api/datasets/rv3@dev/cases/rv3-1/events").json()["events"]
        ]
        assert kinds.count("synced") == 2 and "approved" in kinds


def test_flow_03_overlays_attach_and_are_removed_by_a_whole_sync(cli):
    cli.as_user("akshat")
    cli.suite("base3", {"a.yaml": cases_yaml("ov3-1")})
    cli.ok("sync", "base3")
    overlay = (
        "overlays:\n  - extends: ov3-1\n    agents: [ouragent]\n    tags: [tools]\n"
        "    evaluators:\n      trajectory:\n        required_tools_called: {tools: [search]}\n"
    )
    root = cli.suite("extra3", {"o.yaml": overlay})
    out = cli.ok("sync", "extra3")
    assert sync_counts(out)["overlays_applied"] == 1
    with api(cli.live, "akshat") as akshat:
        [case] = akshat.get("/api/datasets/base3@akshat/cases").json()
        assert "tools" in case["split_tags"]
        (root / "o.yaml").write_text("cases: []\n")
        cli.ok("sync", "extra3")
        [case] = akshat.get("/api/datasets/base3@akshat/cases").json()
        assert "tools" not in case["split_tags"]


def test_flow_03_same_case_id_may_sit_in_two_datasets(cli):
    cli.as_user("akshat")
    cli.suite("twin-a", {"a.yaml": cases_yaml("twin-1")})
    cli.suite("twin-b", {"a.yaml": cases_yaml("twin-1", question="Other text 2024-01-01?")})
    cli.ok("sync", "twin-a")
    out = cli.ok("sync", "twin-b")
    assert sync_counts(out)["created"] == 1
    with api(cli.live, "akshat") as akshat:
        a = akshat.get("/api/datasets/twin-a@akshat/cases").json()[0]
        b = akshat.get("/api/datasets/twin-b@akshat/cases").json()[0]
    assert a["case_id"] == b["case_id"] == "twin-1"
    assert a["content_hash"] != b["content_hash"]


def test_flow_03_lint_errors_abort_the_sync(cli):
    cli.as_user("akshat")
    bad = (
        "cases:\n  - id: bad3-1\n    input: {question: 'q 2024-01-01'}\n"
        "  - id: bad3-1\n    input: {question: 'q 2024-01-02'}\n"
    )
    cli.suite("bad3", {"a.yaml": bad})
    lint = cli.fails("lint", "suites/bad3")
    assert "E-DUP-ID" in lint
    out = cli.fails("sync", "bad3")
    assert "sync aborted on lint errors" in out
    with api(cli.live, "akshat") as akshat:
        assert akshat.get("/api/datasets/bad3@akshat").status_code == 404


def test_flow_03_python_evaluator_case_syncs_with_a_warning(cli):
    cli.as_user("akshat")
    case = (
        "cases:\n  - id: py3-1\n    input: {question: 'q 2024-01-01'}\n"
        "    evaluators:\n      custom:\n        my_python_eval: {x: 1}\n"
    )
    cli.suite("py3", {"a.yaml": case})
    assert "W-CUSTOM-UNKNOWN" in cli.ok("lint", "suites/py3")
    cli.ok("sync", "py3")
    with api(cli.live, "akshat") as akshat:
        assert akshat.get("/api/datasets/py3@akshat").status_code == 200


def test_flow_03_cli_path_errors(cli):
    assert "is not on disk" in cli.fails("sync", "finance@dev")
    assert "no dataset directory 'ghost3'" in cli.fails("sync", "ghost3")
