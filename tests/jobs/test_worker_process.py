import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.db.agents import AgentStore
from evalkit.db.session import make_session_factory
from evalkit.db.spans import SpanStore
from tests.app_run_world import session_spans
from tests.fake_agents import reference_mapping
from tests.identity import seed_identity, workspace_ref

WAIT_SECONDS = 30


def write_config(tmp_path: Path, db_url: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""server:
  base_url: http://evalkit.test
db:
  url: {db_url}
blob:
  url: file://{tmp_path}/blob
otel:
  settle_seconds: 0
worker:
  embedded: false
encryption_key: {Fernet.generate_key().decode()}
"""
    )
    return path


def start_worker(config: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "evalkit.cli.main", "worker", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PGTZ": "UTC"},
    )


def stored_app_runs(db_url: str) -> list[str]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        found = conn.execute(text("SELECT harness_run_id FROM agent_runs WHERE kind = 'app'"))
        runs = [row[0] for row in found.all()]
    engine.dispose()
    return runs


async def test_evalkit_worker_stores_app_runs_and_stops_on_sigterm(tmp_path, db_url):
    factory = make_session_factory(db_url)
    await seed_identity(factory, {"admin": "password", "akshat": "password"}, shared=())
    agent = await AgentStore(factory, "default").create(
        await workspace_ref(factory, "akshat"),
        "support-bot",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
        settings=AgentSettings(app_runs_cron="* * * * *"),
    )
    await SpanStore(factory, "default").upsert(agent.id, session_spans("chat-1", "a"))
    await factory.kw["bind"].dispose()
    worker = start_worker(write_config(tmp_path, db_url))
    deadline = time.monotonic() + WAIT_SECONDS
    while not stored_app_runs(db_url) and time.monotonic() < deadline:
        assert worker.poll() is None, worker.stdout.read()
        time.sleep(0.2)
    worker.send_signal(signal.SIGTERM)
    output, _ = worker.communicate(timeout=WAIT_SECONDS)
    assert stored_app_runs(db_url) == ["chat-1"]
    assert worker.returncode == 0, output
    assert "stored app runs of agent support-bot@akshat: 1 stored, 0 waiting" in output


def test_evalkit_worker_stops_when_the_schema_is_behind(tmp_path, fresh_db_url):
    worker = start_worker(write_config(tmp_path, fresh_db_url))
    output, _ = worker.communicate(timeout=WAIT_SECONDS)
    assert worker.returncode != 0
    assert "evalkit db upgrade" in output
