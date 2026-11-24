from procrastinate import App, BaseConnector, PsycopgConnector
from sqlalchemy.engine import URL

from evalkit.config import WorkerConfig
from evalkit.jobs import app_runs, maintenance

HEARTBEAT_INTERVAL_SECONDS = 10
STALLED_WORKER_TIMEOUT_SECONDS = 120
# Beyond one connection per running job, the worker holds one to listen for
# new jobs and uses others to fetch jobs and write heartbeats.
WORKER_OWN_CONNECTIONS = 3


def job_conninfo(db_url: URL) -> str:
    return db_url.set(drivername="postgresql").render_as_string(hide_password=False)


def pool_size(worker: WorkerConfig) -> int:
    return max(worker.pool_max_size, worker.concurrency + WORKER_OWN_CONNECTIONS)


def build_job_app(
    db_url: URL, worker: WorkerConfig, *, connector: BaseConnector | None = None
) -> App:
    app = App(
        connector=connector
        or PsycopgConnector(conninfo=job_conninfo(db_url), min_size=1, max_size=pool_size(worker)),
        worker_defaults={
            "concurrency": worker.concurrency,
            "delete_jobs": worker.delete_jobs,
            "update_heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS,
            "stalled_worker_timeout": STALLED_WORKER_TIMEOUT_SECONDS,
        },
    )
    app.add_tasks_from(app_runs.build_blueprint(), namespace=app_runs.NAMESPACE)
    app.add_tasks_from(
        maintenance.build_blueprint(worker.job_retention_hours), namespace=maintenance.NAMESPACE
    )
    return app
