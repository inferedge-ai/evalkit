import httpx

from evalkit.contracts.enums import TrajectorySource
from evalkit.core.harness import HarnessClient
from evalkit.core.harness_check import (
    CHECK_BLOB_KEY,
    CHECK_FILE,
    CHECK_FILE_CONTEXT,
    IGNORES_TIME_FILTER,
    check_harness,
)
from tests.fake_agents import FAKE_SETTINGS, fake_agent
from tests.fake_blob import DictBlobStore
from tests.fake_harness import FakeHarness


async def run_check(tmp_path, fake: FakeHarness, blob_store: DictBlobStore | None = None) -> dict:
    return await check_harness(
        fake_agent(),
        "client-key",
        blob_store or DictBlobStore(),
        transport=httpx.ASGITransport(app=fake.app),
    )


async def test_files_check_skipped_when_harness_does_not_accept_files(tmp_path):
    fake = FakeHarness(capabilities_overrides={"accepts": {"files": False}})
    report = await run_check(tmp_path, fake)
    assert report["run"]["status"] == "pass"
    assert report["files"] == {
        "status": "skipped",
        "detail": "accepts.files=false, accepts.file_urls=false",
    }
    assert fake.received_files == {}


async def test_files_check_fails_with_harness_error_body(tmp_path):
    fake = FakeHarness(reject_multipart="Invalid input: expected object, received undefined")
    report = await run_check(tmp_path, fake)
    assert report["run"]["status"] == "fail"
    assert report["files"]["status"] == "fail"
    assert "400" in report["files"]["detail"]
    assert "expected object, received undefined" in report["files"]["detail"]


async def test_files_check_uses_urls_when_accepted(tmp_path):
    fake = FakeHarness(capabilities_overrides={"accepts": {"file_urls": True}})
    blob_store = DictBlobStore()
    report = await run_check(tmp_path, fake, blob_store)
    assert report["run"]["status"] == "pass"
    assert report["files"] == {"status": "pass", "detail": "evalkit-check.txt fetched by url"}
    run_id = next(iter(fake.received_file_urls))
    assert fake.received_file_urls[run_id] == [
        {
            "name": CHECK_FILE[0],
            "url": f"memory://blob/{CHECK_BLOB_KEY}",
            "context": CHECK_FILE_CONTEXT,
        }
    ]
    assert fake.received_files == {}
    stored = await blob_store.get_object(CHECK_BLOB_KEY)
    assert stored == CHECK_FILE[1]


SCHEMA = {
    "type": "object",
    "required": ["question", "source_type"],
    "properties": {"question": {"type": "string"}, "source_type": {"type": "string"}},
}


async def test_check_sends_declared_check_input(tmp_path):
    probe = {"question": "Tag it.", "source_type": "news"}
    fake = FakeHarness(capabilities_overrides={"input_schema": SCHEMA, "check_input": probe})
    report = await run_check(tmp_path, fake)
    assert report["input_schema"] == {
        "status": "pass",
        "detail": "check_input valid against input_schema",
    }
    assert all(p["input"] == probe for p in fake.received_payloads.values())
    assert fake.stop_calls


async def test_input_schema_row_fails_when_default_probe_is_invalid(tmp_path):
    fake = FakeHarness(capabilities_overrides={"input_schema": SCHEMA})
    report = await run_check(tmp_path, fake)
    assert report["input_schema"]["status"] == "fail"
    assert "declare check_input" in report["input_schema"]["detail"]
    assert "source_type" in report["input_schema"]["detail"]


async def test_input_schema_row_uses_default_schema_when_undeclared(tmp_path):
    report = await run_check(tmp_path, FakeHarness())
    assert report["input_schema"] == {
        "status": "pass",
        "detail": "default probe valid against the default input schema",
    }
    assert report["run"]["detail"].endswith("completed with an answer")


async def test_run_row_passes_with_output_only(tmp_path):
    fake = FakeHarness(answer=None, output={"label": "EVENT"})
    report = await run_check(tmp_path, fake)
    assert report["run"]["status"] == "pass"
    assert report["run"]["detail"].endswith("completed with an output")


async def test_trajectory_check_warns_when_subagents_lack_ids(tmp_path):
    doc = {
        "schema_version": "ATIF-v1.8",
        "steps": [{"step_id": 1, "source": "user", "message": "hi"}],
        "subagent_trajectories": [{"schema_version": "ATIF-v1.8", "steps": []}],
    }
    fake = FakeHarness(trajectory_doc=doc)
    report = await run_check(tmp_path, fake)
    assert report["trajectory"]["status"] == "warn"
    assert report["trajectory"]["detail"].startswith("valid ATIF-v1.8 with 1 steps; ")
    assert "trajectory_id" in report["trajectory"]["detail"]


async def test_traces_agent_reports_trajectory_from_traces_and_polls_session(tmp_path):
    from evalkit.core.traces.sessions import SessionTrajectory

    fake = FakeHarness(capabilities_overrides={"provides": {"trajectory": False}})
    agent = fake_agent(
        trajectory_source=TrajectorySource.OTEL,
        settings=FAKE_SETTINGS.model_copy(update={"trajectory_wait_seconds": 1}),
    )

    class Sessions:
        async def trajectory(self, agent, session_id):
            assert agent == "fake@canonical"
            return SessionTrajectory(
                settled=True,
                root_present=True,
                span_count=4,
                trajectory={"schema_version": "ATIF-v1.8", "steps": []},
            )

    report = await check_harness(
        agent,
        "client-key",
        DictBlobStore(),
        transport=httpx.ASGITransport(app=fake.app),
        sessions=Sessions(),
        agent_slug="fake@canonical",
    )
    assert report["trajectory"] == {"status": "pass", "detail": "from traces"}
    assert report["session"]["status"] == "pass" and "4 spans" in report["session"]["detail"]


async def test_run_listing_passes_when_harness_honours_since(tmp_path):
    report = await run_check(tmp_path, FakeHarness())
    assert report["run_listing"]["status"] == "pass"


async def test_run_listing_warns_when_harness_ignores_since(tmp_path):
    report = await run_check(tmp_path, FakeHarness(ignore_time_filter=True))
    assert report["run_listing"]["status"] == "warn"
    assert IGNORES_TIME_FILTER in report["run_listing"]["detail"]


async def test_check_reports_an_unsafe_run_id(tmp_path, monkeypatch):
    async def start_run(self, *args, **kwargs):
        return "a/b"

    monkeypatch.setattr(HarnessClient, "start_run", start_run)
    report = await run_check(tmp_path, FakeHarness())
    assert report["run"] == {
        "status": "fail",
        "detail": "run id 'a/b' must be one plain path segment",
    }
    assert report["trajectory"]["status"] == "fail"
    assert report["stop"]["status"] == "fail"
