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


def check_context(manager: FakeJobManager) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(job_manager=manager), job=SimpleNamespace(id=99, worker_id=7)
    )


def stalled_job(job_id: int, attempts: int = 1, worker_id: int | None = 3) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id, task_name="app_runs:store", attempts=attempts, worker_id=worker_id
    )


async def test_a_stalled_job_is_retried_until_its_attempt_cap_then_failed_and_kept():
    manager = FakeJobManager(
        [stalled_job(1), stalled_job(2, attempts=MAX_STALLED_ATTEMPTS - 1), stalled_job(3, 1, None)]
    )
    assert await retry_stalled_jobs(check_context(manager), 1) == 3
    assert manager.retried == [1, 3]
    assert manager.finished == [(2, Status.FAILED, False)]


async def test_the_check_leaves_itself_and_the_jobs_of_its_own_worker_alone():
    manager = FakeJobManager([stalled_job(99, worker_id=7), stalled_job(5, worker_id=7)])
    await retry_stalled_jobs(check_context(manager), 1)
    assert manager.retried == [] and manager.finished == []


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
