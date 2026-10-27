import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select

from evalkit.core.access import WorkspaceRef
from evalkit.core.importer import import_dataset
from evalkit.core.review import CaseEventKind, CaseReview, CaseState, CaseVersion
from evalkit.db import models
from evalkit.db.cases import current_cases, find_dataset_row, splits
from evalkit.db.fixture_blobs import record_blob
from evalkit.db.review import ReviewLog
from evalkit.db.store import PgStore
from evalkit.store.base import CaseFile, CaseView
from tests.seed import seed_world

CASE_YAML = """
cases:
- id: case-1
  tags: [finance, starbucks]
  input: {{question: "Store count change through FY2021?"}}
  evaluators:
    output:
      answer_rubrics:
        rubrics:
        - rubric: "States 17,133 stores at FY2021 year-end."
          type: factual_retrieval
        - rubric: "{rubric2}"
          type: derived_computation
- id: case-2
  tags: [finance]
  input: {{question: "What changed in FY2022?"}}
  evaluators:
    output:
      answer_rubrics:
        rubrics:
        - rubric: "States the FY2022 delta."
          type: factual_retrieval
        - rubric: "Explains the driver."
          type: analysis_interpretation
"""

OVERLAY_YAML = """
overlays:
- extends: case-1
  agents: [support-bot]
  tags: [client-priority]
  evaluators:
    custom:
      required_tool_calls: {tools: [retrieve_passages]}
"""


V1 = hashlib.sha256(b"%PDF v1").hexdigest()
V2 = hashlib.sha256(b"%PDF v2").hexdigest()
SUPPORT_BOT_BLOCK_EVALUATORS = {"custom": {"required_tool_calls": {"tools": ["retrieve_passages"]}}}


@pytest.fixture
async def ctx(session_factory):
    world = await seed_world(session_factory)
    return session_factory, PgStore(session_factory), world


def write_suite(tmp_path: Path, rubric2: str = "Computes the CAGR for FY2021.") -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(exist_ok=True)
    (suite / "cases.yaml").write_text(CASE_YAML.format(rubric2=rubric2))
    return suite


async def do_import(
    ctx,
    files,
    *,
    dataset: str = "finance",
    workspace: WorkspaceRef | None = None,
    full: bool = True,
    known: set[str] | None = None,
    detail: dict | None = None,
    root: Path | None = None,
    actor: str = "admin",
):
    _, store, world = ctx
    return await import_dataset(
        store,
        workspace=workspace or world.canonical,
        dataset=dataset,
        files=files,
        full=full,
        known_custom_evaluators={"required_tool_calls"} if known is None else known,
        actor_id=world.admin_id if actor == "admin" else world.akshat_id,
        detail=detail or {},
        root=root,
    )


async def views(ctx, dataset: str = "finance", workspace: str = "canonical", agent=None):
    sf, _, _ = ctx
    async with sf() as s:
        row = await find_dataset_row(s, workspace, dataset)
        if row is None:
            return {}
        return {v.case_id: v for v in await current_cases(s, row, agent=agent)}


async def split_counts(ctx, dataset: str = "finance", workspace: str = "canonical") -> dict:
    sf, _, _ = ctx
    async with sf() as s:
        row = await find_dataset_row(s, workspace, dataset)
        assert row is not None
        return dict(await splits(s, row))


async def last_sync_detail(ctx, dataset: str = "finance", workspace: str = "canonical") -> dict:
    sf, _, _ = ctx
    async with sf() as s:
        row = await find_dataset_row(s, workspace, dataset)
        assert row is not None and row.last_sync is not None
        return row.last_sync.detail


async def put_blob(ctx, sha: str) -> None:
    sf, _, world = ctx
    async with sf() as s:
        await record_blob(s, sha, 1, world.admin_id)
        await s.commit()


async def test_fresh_import_creates_all(tmp_path, ctx):
    suite = write_suite(tmp_path)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.created == 2 and report.patched == 0 and not report.aborted
    cases = await views(ctx)
    assert set(cases) == {"case-1", "case-2"}
    assert (await split_counts(ctx))["starbucks"] == 1
    assert cases["case-1"].content_hash and cases["case-1"].evaluators["output"]["answer_rubrics"]


async def test_second_import_noops(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.created == 0 and report.patched == 0 and report.deleted == 0


async def test_rubric_edit_patches_one(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    write_suite(tmp_path, rubric2="Computes the CAGR for FY2021 exactly.")
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.patched == 1 and report.created == 0


async def test_prune_deletes_stale(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    (suite / "cases.yaml").write_text(
        "- id: case-1\n  tags: [finance]\n"
        "  input: {question: 'Store count change through FY2021?'}\n"
    )
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.deleted == 1
    assert set(await views(ctx)) == {"case-1"}


async def test_lint_error_aborts(tmp_path, ctx):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "bad.yaml").write_text("- id: a\n  tags: ['working:x']\n  input: {question: q}\n")
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.aborted
    assert await views(ctx) == {}


async def import_overlay(ctx, tmp_path, *, dataset="client-cases", body=OVERLAY_YAML):
    overlay_dir = tmp_path / dataset
    overlay_dir.mkdir(exist_ok=True)
    (overlay_dir / "overlays.yaml").write_text(body)
    return await do_import(ctx, sorted(overlay_dir.glob("*.yaml")), dataset=dataset)


async def overlay_blocks(ctx, exclude: str = "finance") -> dict:
    _, store, world = ctx
    blocks = await store.overlays_on(world.canonical, ["case-1"], exclude_dataset=exclude)
    return {b.source: b for b in blocks.get("case-1", [])}


async def assert_overlay_present(ctx, case: CaseView, source="client-cases"):
    block = (await overlay_blocks(ctx))[source]
    assert block.agents == ["support-bot"]
    assert block.tags == ["client-priority"]
    assert block.evaluators == SUPPORT_BOT_BLOCK_EVALUATORS
    assert block.source_file == "overlays.yaml"
    assert "custom" not in case.evaluators
    assert "client-priority" not in case.tags


async def test_overlay_is_stored_beside_the_case_config_and_removed(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    before = (await views(ctx))["case-1"]

    report = await import_overlay(ctx, tmp_path)
    assert report.overlays_applied == 1
    case = (await views(ctx, agent="support-bot"))["case-1"]
    await assert_overlay_present(ctx, case)
    assert case.evaluators == before.evaluators
    assert case.tags == before.tags
    assert case.content_hash == before.content_hash
    assert case.effective_evaluators["custom"] == SUPPORT_BOT_BLOCK_EVALUATORS["custom"]
    assert (await split_counts(ctx))["client-priority"] == 1

    report = await import_overlay(ctx, tmp_path, body="overlays: []\n")
    assert report.overlays_applied == 0
    after = (await views(ctx, agent="support-bot"))["case-1"]
    assert after.effective_evaluators == before.evaluators
    assert "client-priority" not in await split_counts(ctx)


FIN_OVERLAY_YAML = """
overlays:
- extends: case-1
  agents: [{agent}]
  evaluators:
    custom:
      required_tool_calls: {{tools: [search]}}
"""


async def test_two_overlay_datasets_extend_one_case_side_by_side(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    await import_overlay(ctx, tmp_path)
    report = await import_overlay(
        ctx, tmp_path, dataset="fin-cases", body=FIN_OVERLAY_YAML.format(agent="research-agent")
    )
    assert not report.aborted and report.overlays_applied == 1
    await assert_overlay_present(ctx, (await views(ctx))["case-1"])
    assert (await overlay_blocks(ctx))["fin-cases"].agents == ["research-agent"]

    await import_overlay(ctx, tmp_path, body="overlays: []\n")
    assert set(await overlay_blocks(ctx)) == {"fin-cases"}


async def test_overlay_clash_aborts_the_sync(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    await import_overlay(ctx, tmp_path)
    before = await overlay_blocks(ctx)

    report = await import_overlay(
        ctx, tmp_path, dataset="fin-cases", body=FIN_OVERLAY_YAML.format(agent="support-bot")
    )
    assert report.aborted
    assert [(f.code, f.case_id) for f in report.findings] == [("E-OVERLAY-CLASH", "case-1")]
    assert await overlay_blocks(ctx) == before


async def test_resync_of_target_suite_keeps_overlays(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    report = await import_overlay(ctx, tmp_path)
    assert report.overlays_applied == 1
    before = (await views(ctx))["case-1"]

    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted and report.patched == 0
    assert (await views(ctx))["case-1"] == before

    write_suite(tmp_path, rubric2="Computes the CAGR for FY2021 to one decimal.")
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.patched == 1
    case = (await views(ctx))["case-1"]
    assert case.content_hash != before.content_hash
    await assert_overlay_present(ctx, case)
    assert (await split_counts(ctx))["client-priority"] == 1


async def test_unrelated_suite_sync_does_not_strip_other_suites_overlays(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    await import_overlay(ctx, tmp_path)

    other = tmp_path / "other"
    other.mkdir()
    (other / "cases.yaml").write_text(
        "- id: other-1\n  input: {question: 'unrelated as of 2024-01-01'}\n"
    )
    report = await do_import(ctx, sorted(other.glob("*.yaml")), dataset="other", known=set())
    assert not report.aborted and report.overlays_applied == 0
    await assert_overlay_present(ctx, (await views(ctx))["case-1"])

    report = await import_overlay(ctx, tmp_path, body="overlays: []\n")
    assert report.overlays_applied == 0
    assert await overlay_blocks(ctx) == {}


async def test_shared_registry_suites_with_disjoint_keys_both_sync(tmp_path, ctx):
    registry = tmp_path / "fixtures.yaml"
    registry.write_text(
        f"fixtures:\n- key: a.pdf\n  sha256: {V1}\n  context: A\n"
        f"- key: b.pdf\n  sha256: {V2}\n  context: B\n"
    )
    await put_blob(ctx, V1)
    await put_blob(ctx, V2)
    for slug, key in (("suite-a", "a.pdf"), ("suite-b", "b.pdf")):
        suite_dir = tmp_path / slug
        suite_dir.mkdir()
        (suite_dir / "cases.yaml").write_text(
            f"- id: {slug}-1\n  input: {{question: 'q as of 2024-01-01'}}\n  fixtures: [{key}]\n"
        )
        report = await do_import(
            ctx, [suite_dir / "cases.yaml", registry], dataset=slug, known=set()
        )
        assert not report.aborted, report.findings
        assert report.findings == []


async def test_directory_sync_prunes_to_empty(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert len(await views(ctx)) == 2
    (suite / "cases.yaml").unlink()
    (suite / "fixtures.yaml").write_text("fixtures: []\n")
    report = await do_import(ctx, sorted(suite.glob("*.yaml")), detail={"sha": "v2"})
    assert not report.aborted and report.deleted == 2
    assert await views(ctx) == {}
    assert (await last_sync_detail(ctx))["sha"] == "v2"
    report = await do_import(ctx, [])
    assert not report.aborted and report.deleted == 0


async def test_sync_of_unknown_dataset_with_no_cases_creates_nothing(ctx):
    sf, _, _ = ctx
    report = await do_import(ctx, [])
    assert not report.aborted and report.deleted == 0
    async with sf() as s:
        assert await find_dataset_row(s, "canonical", "finance") is None


async def test_same_case_id_in_two_datasets_of_a_workspace_is_allowed(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    other = tmp_path / "other"
    other.mkdir()
    (other / "own.yaml").write_text(
        "- id: case-1\n  input: {question: 'my own q as of 2024-01-01'}\n"
    )
    report = await do_import(
        ctx, sorted(other.glob("*.yaml")), dataset="client-cases", full=False, known=set()
    )
    assert not report.aborted, report.findings
    assert (await views(ctx, "client-cases"))["case-1"].input["question"].startswith("my own")
    assert (await views(ctx))["case-1"].input["question"].startswith("Store count")


async def test_resync_of_own_cases_is_not_a_collision(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted and report.findings == []


async def test_sync_detail_is_recorded_per_sync(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")), detail={"sha": "abc123"})
    assert (await last_sync_detail(ctx))["sha"] == "abc123"
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert "sha" not in await last_sync_detail(ctx)


def write_fixture_suite(tmp_path: Path, *, context: str = "A PDF.", sha256: str = V1) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(exist_ok=True)
    (suite / "fixtures.yaml").write_text(
        f"fixtures:\n- key: doc.pdf\n  sha256: {sha256}\n  context: {context}\n"
    )
    (suite / "cases.yaml").write_text(
        "- id: fx-1\n  input: {question: 'Summarize as of 2024-01-01.'}\n  fixtures: [doc.pdf]\n"
    )
    return suite


async def test_fixture_resolution_from_uploaded_blobs(tmp_path, ctx):
    suite = write_fixture_suite(tmp_path)
    await put_blob(ctx, V1)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted, report.findings
    case = (await views(ctx))["fx-1"]
    assert case.files == [CaseFile(key="doc.pdf", sha256=V1, context="A PDF.")]
    assert case.input == {"question": "Summarize as of 2024-01-01."}
    first_hash = case.content_hash

    write_fixture_suite(tmp_path, context="A different description.")
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert (await views(ctx))["fx-1"].content_hash != first_hash


async def test_fixture_never_uploaded_aborts(tmp_path, ctx):
    suite = write_fixture_suite(tmp_path)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.aborted
    errors = [f for f in report.findings if f.severity == "error"]
    assert [f.code for f in errors] == ["E-FIXTURE-MISSING"]
    assert errors[0].message.startswith(f"fixture 'doc.pdf' (sha256 {V1}) was never uploaded; ")


async def test_registry_yaml_is_not_a_case_file(tmp_path, ctx):
    suite = write_fixture_suite(tmp_path)
    await put_blob(ctx, V1)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted
    assert set(await views(ctx)) == {"fx-1"}


async def test_case_without_registry_in_upload_is_unknown_key(tmp_path, ctx):
    suite = write_fixture_suite(tmp_path)
    await put_blob(ctx, V1)
    report = await do_import(ctx, [suite / "cases.yaml"])
    assert report.aborted
    assert [f.code for f in report.findings] == ["E-FIXTURE-UNKNOWN-KEY"]


async def test_registry_digest_change_needs_the_new_bytes_uploaded(tmp_path, ctx):
    suite = write_fixture_suite(tmp_path)
    await put_blob(ctx, V1)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted, report.findings

    write_fixture_suite(tmp_path, sha256=V2)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.aborted
    errors = [f for f in report.findings if f.severity == "error"]
    assert [f.code for f in errors] == ["E-FIXTURE-MISSING"]
    assert (await views(ctx))["fx-1"].files[0].sha256 == V1

    await put_blob(ctx, V2)
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert not report.aborted, report.findings
    assert (await views(ctx))["fx-1"].files[0].sha256 == V2


async def test_datasets_may_share_a_fixture_key_with_different_bytes(tmp_path, ctx):
    suite_a = write_fixture_suite(tmp_path)
    await put_blob(ctx, V1)
    await put_blob(ctx, V2)
    report_a = await do_import(ctx, sorted(suite_a.glob("*.yaml")), dataset="suite-a", known=set())
    assert not report_a.aborted, report_a.findings

    suite_b = tmp_path / "suite-b"
    suite_b.mkdir()
    (suite_b / "fixtures.yaml").write_text(
        f"fixtures:\n- key: doc.pdf\n  sha256: {V2}\n  context: B's file\n"
    )
    (suite_b / "cases.yaml").write_text(
        "- id: b-1\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    )
    report_b = await do_import(ctx, sorted(suite_b.glob("*.yaml")), dataset="suite-b", known=set())
    assert not report_b.aborted, report_b.findings
    assert (await views(ctx, "suite-a"))["fx-1"].files[0].sha256 == V1
    assert (await views(ctx, "suite-b"))["b-1"].files[0].sha256 == V2


async def current_hash_of(ctx, case_id):
    return (await views(ctx))[case_id].content_hash


async def synced_count(ctx) -> int:
    sf, _, _ = ctx
    async with sf() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(models.CaseEvent)
            .where(models.CaseEvent.kind == CaseEventKind.SYNCED.value)
        )
    return count or 0


async def test_sync_records_synced_events_with_actor(tmp_path, ctx):
    sf, _, _ = ctx
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")), actor="akshat")
    review = ReviewLog(sf)
    for case_id in ("case-1", "case-2"):
        [event] = await review.history("finance@canonical", case_id)
        assert event.kind is CaseEventKind.SYNCED and event.actor == "akshat"
        assert event.content_hash == await current_hash_of(ctx, case_id)


async def test_unchanged_resync_records_nothing(tmp_path, ctx):
    files = sorted(write_suite(tmp_path).glob("*.yaml"))
    await do_import(ctx, files)
    before = await synced_count(ctx)
    report = await do_import(ctx, files)
    assert report.patched == 0 and await synced_count(ctx) == before


async def test_edit_of_approved_case_reports_now_unreviewed(tmp_path, ctx):
    sf, _, _ = ctx
    review = ReviewLog(sf)
    files = sorted(write_suite(tmp_path).glob("*.yaml"))
    await do_import(ctx, files)
    await review.append(
        [
            CaseReview.unreviewed(
                "finance@canonical", "case-1", await current_hash_of(ctx, "case-1")
            ).approve("admin")
        ]
    )

    files = sorted(write_suite(tmp_path, rubric2="Computes the CAGR to FY2023.").glob("*.yaml"))
    report = await do_import(ctx, files)
    assert report.review.now_unreviewed == ["case-1"] and report.review.carried == []
    loaded = await review.load(
        "finance@canonical", [CaseVersion("case-1", await current_hash_of(ctx, "case-1"))]
    )
    assert loaded["case-1"].state() is CaseState.UNREVIEWED


async def test_edit_under_review_carries_the_request_over(tmp_path, ctx):
    sf, _, _ = ctx
    review = ReviewLog(sf)
    files = sorted(write_suite(tmp_path).glob("*.yaml"))
    await do_import(ctx, files)
    r = CaseReview.unreviewed("finance@canonical", "case-1", await current_hash_of(ctx, "case-1"))
    await review.append([r.request_review("akshat", "admin"), r.request_changes("admin")])

    files = sorted(write_suite(tmp_path, rubric2="Computes the CAGR to FY2023.").glob("*.yaml"))
    report = await do_import(ctx, files, actor="akshat")
    assert [(c.case_id, c.reviewer) for c in report.review.carried] == [("case-1", "admin")]
    loaded = await review.load(
        "finance@canonical", [CaseVersion("case-1", await current_hash_of(ctx, "case-1"))]
    )
    assert loaded["case-1"].state() is CaseState.REVIEW_REQUESTED
    assert loaded["case-1"].requested_to == "admin"
    assert loaded["case-1"].last_event is not None
    assert loaded["case-1"].last_event.actor == "akshat"


TOOL_OVERLAY_YAML = """
overlays:
- extends: case-1
  agents: [support-bot]
  evaluators:
    trajectory:
      required_tools_called: {tools: [get_news]}
"""


async def test_overlay_trajectory_evaluators_are_stored_carried_and_removed(tmp_path, ctx):
    suite = write_suite(tmp_path)
    await do_import(ctx, sorted(suite.glob("*.yaml")))
    report = await import_overlay(ctx, tmp_path, body=TOOL_OVERLAY_YAML)
    assert report.overlays_applied == 1
    expected = {"trajectory": {"required_tools_called": {"tools": ["get_news"]}}}
    assert (await overlay_blocks(ctx))["client-cases"].evaluators == expected
    assert "trajectory" not in (await views(ctx))["case-1"].evaluators

    write_suite(tmp_path, rubric2="Computes the CAGR for FY2021 to one decimal.")
    report = await do_import(ctx, sorted(suite.glob("*.yaml")))
    assert report.patched == 1
    assert (await overlay_blocks(ctx))["client-cases"].evaluators == expected
    case = (await views(ctx, agent="support-bot"))["case-1"]
    assert case.effective_evaluators["trajectory"] == expected["trajectory"]

    await import_overlay(ctx, tmp_path, body="overlays: []\n")
    assert await overlay_blocks(ctx) == {}


async def test_path_components_become_tags(tmp_path, ctx):
    suite = tmp_path / "suite"
    (suite / "statement-analysis").mkdir(parents=True)
    (suite / "statement-analysis" / "cases.yaml").write_text(CASE_YAML.format(rubric2="Computes."))
    report = await do_import(ctx, sorted(suite.rglob("*.yaml")), root=suite)
    assert not report.aborted
    assert (await views(ctx))["case-1"].tags == ["finance", "starbucks", "statement-analysis"]
    assert "statement-analysis" in await split_counts(ctx)


async def test_workspace_sync_needs_the_fixture_uploaded(tmp_path, ctx):
    _, _, world = ctx
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "cases.yaml").write_text(
        "cases:\n- id: fx-1\n  fixtures: [bs.csv]\n  input: {question: 'Net debt in FY2025?'}\n"
        "  evaluators: {output: {answer_rubrics: {rubrics: "
        "[{rubric: 'States it.', type: factual_retrieval}]}}}\n"
    )
    digest = hashlib.sha256(b"local bytes").hexdigest()
    (suite / "fixtures.yaml").write_text(
        f"fixtures:\n- key: bs.csv\n  sha256: {digest}\n  context: Balance sheet.\n"
    )
    files = sorted(suite.glob("*.yaml"))
    detail = {"branch": "feat", "dirty": True}
    missing = await do_import(
        ctx, files, workspace=world.akshat_ws, known=set(), detail=detail, actor="akshat"
    )
    assert missing.aborted
    error = next(f for f in missing.findings if f.code == "E-FIXTURE-MISSING")
    assert "uploads it" in error.message
    await put_blob(ctx, digest)
    ok = await do_import(
        ctx, files, workspace=world.akshat_ws, known=set(), detail=detail, actor="akshat"
    )
    assert not ok.aborted
    case = (await views(ctx, workspace="akshat"))["fx-1"]
    assert case.files == [CaseFile(key="bs.csv", sha256=digest, context="Balance sheet.")]
    assert await last_sync_detail(ctx, workspace="akshat") == {
        **detail,
        "counts": {"created": 1, "patched": 0, "removed": 0, "unchanged": 0},
    }


async def test_same_case_in_another_workspace_is_not_a_collision(tmp_path, ctx):
    _, _, world = ctx
    files = sorted(write_suite(tmp_path).glob("*.yaml"))
    assert not (await do_import(ctx, files)).aborted
    mine = await do_import(ctx, files, workspace=world.akshat_ws, actor="akshat")
    assert not mine.aborted and mine.created == 2
    other = await do_import(ctx, files, dataset="other", workspace=world.akshat_ws, actor="akshat")
    assert not other.aborted and other.created == 2
