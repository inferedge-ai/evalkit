import logging

from procrastinate import Blueprint, JobContext, builtin_tasks
from procrastinate.jobs import Status

LOGGER = logging.getLogger(__name__)

NAMESPACE = "maintenance"
STALLED_JOB_HEARTBEAT_SECONDS = 120
MAX_STALLED_ATTEMPTS = 5
RETRY_STALLED_CRON = "*/2 * * * *"
REMOVE_OLD_CRON = "0 4 * * *"


async def retry_stalled_jobs(context: JobContext, timestamp: int) -> int:
    job_manager = context.app.job_manager
    stalled = list(
        await job_manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_JOB_HEARTBEAT_SECONDS)
    )
    for job in stalled:
        # After a long pause every job of this worker looks stalled, this check
        # included. The worker running the check is alive, so its jobs are not.
        if job.id == context.job.id or (
            job.worker_id is not None and job.worker_id == context.job.worker_id
        ):
            continue
        if job.attempts + 1 >= MAX_STALLED_ATTEMPTS:
            LOGGER.error(
                "stalled job %s (%s) failed after %s attempts",
                job.id,
                job.task_name,
                job.attempts + 1,
            )
            await job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
            continue
        await job_manager.retry_job(job)
    return len(stalled)


# A blueprint joins one app only, so every app gets its own.
def build_blueprint(job_retention_hours: int) -> Blueprint:
    blueprint = Blueprint()

    async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
        await builtin_tasks.remove_old_jobs(
            context,
            max_hours=job_retention_hours,
            remove_failed=True,
            remove_cancelled=True,
            remove_aborted=True,
        )

    blueprint.periodic(cron=RETRY_STALLED_CRON)(
        blueprint.task(
            name="retry_stalled_jobs",
            lock="retry_stalled_jobs",
            queueing_lock="retry_stalled_jobs",
            pass_context=True,
        )(retry_stalled_jobs)
    )
    blueprint.periodic(cron=REMOVE_OLD_CRON)(
        blueprint.task(
            name="remove_old_jobs",
            lock="remove_old_jobs",
            queueing_lock="remove_old_jobs",
            pass_context=True,
        )(remove_old_jobs)
    )
    return blueprint
