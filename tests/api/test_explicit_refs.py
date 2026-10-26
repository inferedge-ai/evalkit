from tests.fake_harness import FakeHarness
from tests.native import seed_env, serve


async def test_bare_refs_are_rejected(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    async with serve(env, tmp_path) as client:
        for path in ("/api/datasets/finance", "/api/datasets/finance/cases"):
            resp = await client.get(path)
            assert resp.status_code == 400
            assert resp.json()["detail"] == "use name@workspace, not 'finance'"
        resp = await client.post(
            "/api/experiments", json={"dataset": "finance@canonical", "agent": "fake"}
        )
        assert resp.status_code == 400
        assert (await client.get("/api/datasets/finance@canonical")).status_code == 200
