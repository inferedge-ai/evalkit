import httpx2
import pytest
from fastapi import APIRouter

from evalkit.api.app import create_app
from evalkit.api.auth import CurrentUser
from tests.native import HOLDER, USERS, seed_env

billing = APIRouter(prefix="/api/billing")


@billing.get("/plan")
async def get_plan(user: CurrentUser) -> dict:
    return {"plan": "team", "user": user.username}


@billing.post("/plan")
async def change_plan() -> dict:
    return {"changed": True}


clash = APIRouter()


@clash.post("/mcp")
async def take_mcp() -> dict:
    return {"from": "wrapper"}


@pytest.fixture
async def client(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>evalkit spa</html>")
    app = create_app(
        env.deps.cfg,  # type: ignore[arg-type]
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=session_factory,
        check_schema=False,
        ui_dist=dist,
        holder=HOLDER,
        routers=[billing, clash],
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_serves_wrapper_routes(client):
    signed_out = await client.get("/api/billing/plan")
    signed_out_change = await client.post("/api/billing/plan")
    mcp = await client.post("/mcp", json={})
    login = await client.post(
        "/api/auth/login", json={"username": "admin", "password": USERS["admin"]}
    )
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"

    plan = await client.get("/api/billing/plan")
    changed = await client.post("/api/billing/plan")
    page = await client.get("/billing")
    me = await client.get("/api/auth/me")
    unknown = await client.get("/api/billing/nope")

    assert signed_out.status_code == 401
    assert signed_out_change.status_code == 401
    assert mcp.status_code == 401 and "WWW-Authenticate" in mcp.headers
    assert plan.status_code == 200 and plan.json() == {"plan": "team", "user": "admin"}
    assert changed.json() == {"changed": True}
    assert page.status_code == 200 and "evalkit spa" in page.text
    assert me.json()["username"] == "admin"
    assert unknown.status_code == 404
