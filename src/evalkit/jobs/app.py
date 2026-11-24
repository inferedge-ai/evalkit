from procrastinate import App, BaseConnector, PsycopgConnector
from sqlalchemy.engine import URL

from evalkit.jobs import app_runs

POOL_MAX_SIZE = 4


def job_conninfo(db_url: URL) -> str:
    return db_url.set(drivername="postgresql").render_as_string(hide_password=False)


def build_job_app(db_url: URL, *, connector: BaseConnector | None = None) -> App:
    app = App(
        connector=connector
        or PsycopgConnector(conninfo=job_conninfo(db_url), max_size=POOL_MAX_SIZE)
    )
    app.add_tasks_from(app_runs.build_blueprint(), namespace=app_runs.NAMESPACE)
    return app
