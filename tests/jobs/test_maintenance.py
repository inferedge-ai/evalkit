from dataclasses import dataclass, field
from types import SimpleNamespace

from procrastinate import testing
from procrastinate.jobs import Status
from sqlalchemy.engine import make_url

from evalkit.config import WorkerConfig
from evalkit.jobs.app import build_job_app, pool_size
from evalkit.jobs.maintenance import MAX_STALLED_ATTEMPTS, retry_stalled_jobs

URL = make_url("postgresql+psycopg://localhost/unused")


@dataclass
class FakeJobManager:
    stalled: list
    retried: list = field(default_factory=list)
    finished: list = field(default_factory=list)

    async def get_stalled_jobs(self, seconds_since_heartbeat: int):
        return self.stalled

    async def retry_job(self, job) -> None:
        self.retried.append(job.id)

    async def finish_job(self, job, status: Status, delete_job: bool) -> None:
        self.finished.append((job.id, status, delete_job))


async def test_a_stalled_job_is_retried_until_its_attempt_cap_then_failed_and_kept():
    young = SimpleNamespace(id=1, task_name="app_runs:store", attempts=1)
    spent = SimpleNamespace(id=2, task_name="app_runs:store", attempts=MAX_STALLED_ATTEMPTS - 1)
    manager = FakeJobManager([young, spent])
    context = SimpleNamespace(app=SimpleNamespace(job_manager=manager))
    assert await retry_stalled_jobs(context, 1) == 2
    assert manager.retried == [1]
    assert manager.finished == [(2, Status.FAILED, False)]


def test_the_job_app_keeps_finished_jobs_and_schedules_its_maintenance():
    app = build_job_app(URL, WorkerConfig(), connector=testing.InMemoryConnector())
    assert app.worker_defaults["delete_jobs"] == "never"
    scheduled = {
        name: task.cron for (name, _), task in app.periodic_registry.periodic_tasks.items()
    }
    assert scheduled == {
        "app_runs:tick": "* * * * *",
        "maintenance:retry_stalled_jobs": "*/2 * * * *",
        "maintenance:remove_old_jobs": "0 4 * * *",
    }


def test_the_worker_pool_has_room_for_every_running_job():
    assert pool_size(WorkerConfig()) == 5
    assert pool_size(WorkerConfig(concurrency=1, pool_max_size=10)) == 10
