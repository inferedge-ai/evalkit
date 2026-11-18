from evalkit.contracts.cases import Case
from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.core.importer import ImportReport, import_cases
from evalkit.db.store import PgStore
from evalkit.db.workspaces import WorkspaceStore
from tests.api.test_promotion_api import CASES_YAML, _cases, _decide, env  # noqa: F401

REFUSAL = "was written by an extension of this server and cannot be changed"


async def write_by_extension(
    ctx, dataset: str, workspace: str, case_ids: list[str]
) -> ImportReport:
    cases = [
        Case.model_validate({"id": case_id, "input": {"question": f"What is {case_id}?"}})
        for case_id in case_ids
    ]
    return await import_cases(
        PgStore(ctx.session_factory, "default"),
        workspace=await WorkspaceStore(ctx.session_factory, "default").get_by_name(workspace),
        dataset=dataset,
        kind=DatasetKind.SUITE,
        sourced_cases=[(case, None) for case in cases],
        overlays=[],
        findings=[],
        full=True,
        known_custom_evaluators=set(),
        actor_id=ctx.user_ids["admin"],
        source=SyncSource.EXTENSION,
        detail={"written_by": "tests"},
    )


async def _sync(client, dataset: str, workspace: str):
    return await client.post(
        "/api/sync",
        params={"dataset": dataset, "workspace": workspace},
        files=[("files", ("cases.yaml", CASES_YAML, "application/octet-stream"))],
    )


async def test_a_user_cannot_sync_into_a_dataset_an_extension_wrote(env):  # noqa: F811
    clients, ctx = env
    await write_by_extension(ctx, "bench", "dev", ["bench-1", "bench-2"])
    refused = await _sync(clients["admin"], "bench", "dev")
    assert refused.status_code == 409
    assert refused.json()["detail"] == f"bench@dev {REFUSAL}"
    assert list(await _cases(clients["admin"], "bench@dev")) == ["bench-1", "bench-2"]

    again = await write_by_extension(ctx, "bench", "dev", ["bench-1"])
    assert (again.aborted, again.deleted) == (False, 1)
    assert list(await _cases(clients["admin"], "bench@dev")) == ["bench-1"]

    assert (await _sync(clients["admin"], "finance", "dev")).status_code == 200


async def test_an_extension_writes_into_a_promotion_target_and_promotion_into_it_is_refused(
    env,  # noqa: F811
):
    clients, ctx = env
    report = await write_by_extension(ctx, "finance", "prod", ["bench-1"])
    assert (report.aborted, report.created) == (False, 1)
    refused = await _sync(clients["admin"], "finance", "prod")
    assert refused.status_code == 409
    assert refused.json()["detail"] == f"finance@prod {REFUSAL}"

    approval = await _decide(clients["sme"], "finance@dev", "case-1", "approve")
    assert approval.status_code == 409
    assert approval.json()["detail"] == f"finance@prod {REFUSAL}"
    assert list(await _cases(clients["sme"], "finance@prod")) == ["bench-1"]
    dev = await _cases(clients["sme"], "finance@dev")
    assert dev["case-1"]["review"]["state"] == "unreviewed"


async def test_review_does_not_apply_to_a_dataset_an_extension_wrote(env):  # noqa: F811
    clients, ctx = env
    await write_by_extension(ctx, "bench", "dev", ["bench-1"])
    cases = await _cases(clients["sme"], "bench@dev")
    assert cases["bench-1"]["review"] is None

    message = "bench@dev was written by an extension of this server; review does not apply"
    requested = await clients["alice"].post(
        "/api/datasets/bench@dev/review-requests",
        json={"reviewer": "sme", "selection": {"all": True}},
    )
    assert (requested.status_code, requested.json()["detail"]) == (409, message)
    decided = await _decide(clients["sme"], "bench@dev", "bench-1", "approve")
    assert (decided.status_code, decided.json()["detail"]) == (409, message)
    queue = (await clients["sme"].get("/api/review/queue", params={"workspace": "dev"})).json()
    assert all(item["dataset"] != "bench@dev" for item in queue["items"])
