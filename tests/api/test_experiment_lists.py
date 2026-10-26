from tests.fake_harness import FakeHarness
from tests.native import seed_env, serve, wait_for


async def test_list_rows_carry_progress_scores_cost_and_latency(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    async with serve(env, tmp_path) as client:
        created = await client.post(
            "/api/experiments", json={"dataset": "finance@canonical", "agent": "fake@canonical"}
        )
        exp_id = created.json()["id"]
        await wait_for(client, exp_id)
        [row] = (await client.get("/api/experiments", params={"workspace": "canonical"})).json()
        [same] = (await client.get("/api/datasets/finance@canonical/experiments")).json()
    assert row == same
    assert row["id"] == exp_id and row["number"] == 1
    assert row["progress"] == {"total": 2, "ran": 2, "evaluated": 2}
    assert row["scores"]["content"] == 0.5
    assert row["cost_usd"] is not None and row["cost_usd"] >= 0
    assert row["avg_latency_ms"] is not None and row["avg_latency_ms"] >= 0
