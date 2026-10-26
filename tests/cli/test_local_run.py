import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest
import typer

from evalkit.cli.client import ApiClient
from evalkit.client.sync import git_revision, resolve_path, sync_files, upload_fixtures
from evalkit.core.workspace import Reference
from evalkit.store.base import GitRevision, Selection

CASE = "cases:\n- id: {id}\n  input: {{question: 'q {id}'}}\n"


@pytest.fixture
def cases_root(tmp_path):
    root = tmp_path / "cases"
    (root / "finance" / "statement-analysis").mkdir(parents=True)
    (root / "finance" / "statement-analysis" / "meridian.yaml").write_text(CASE.format(id="m-1"))
    (root / "finance" / "mf.yaml").write_text(CASE.format(id="mf-1"))
    (tmp_path / "fixtures.yaml").write_text("fixtures: []\n")
    return root


def test_bare_name_is_my_workspace_and_whole_dataset(cases_root):
    r = resolve_path("finance", cases_root, "akshat")
    assert r.ref == Reference("finance", "akshat")
    assert r.dataset_dir == cases_root / "finance"
    assert r.selection == Selection(all=True)


def test_subdirectory_selects_tag_and_file_selects_case_ids(cases_root):
    r = resolve_path("finance/statement-analysis", cases_root, "akshat")
    assert r.selection == Selection(tag="statement-analysis")
    r = resolve_path("finance/statement-analysis/meridian.yaml", cases_root, "akshat")
    assert r.selection == Selection(case_ids=["m-1"])


def test_filesystem_path_resolves_through_cases_root(cases_root, monkeypatch):
    monkeypatch.chdir(cases_root.parent)
    r = resolve_path("cases/finance/mf.yaml", cases_root, "akshat")
    assert r.ref.name == "finance" and r.selection == Selection(case_ids=["mf-1"])
    assert r.dataset_dir == cases_root / "finance" and r.subpath == "mf.yaml"
    r = resolve_path("cases/finance/statement-analysis", cases_root, "akshat")
    assert r.ref.name == "finance" and r.subpath == "statement-analysis"
    assert r.selection == Selection(tag="statement-analysis")
    r = resolve_path("cases/finance", cases_root, "akshat")
    assert r.subpath is None and r.selection == Selection(all=True)


def test_cases_root_itself_is_rejected(cases_root):
    with pytest.raises(ValueError, match="cases root"):
        resolve_path(str(cases_root), cases_root, "akshat")


def test_sync_files_rejects_a_subpath_that_is_only_a_tag(cases_root):
    with pytest.raises(ValueError, match="not a folder or file"):
        sync_files(cases_root / "finance", "statement-only-tag")


def test_filesystem_path_outside_cases_root_is_its_own_dataset(cases_root, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "scratch"
    (elsewhere / "sub").mkdir(parents=True)
    (elsewhere / "sub" / "a.yaml").write_text(CASE.format(id="s-1"))
    r = resolve_path(str(elsewhere), cases_root, "akshat")
    assert r.ref.name == "scratch" and r.dataset_dir == elsewhere and r.subpath is None
    r = resolve_path(str(elsewhere / "sub" / "a.yaml"), None, "akshat")
    assert r.ref.name == "sub" and r.subpath == "a.yaml"
    assert r.selection == Selection(case_ids=["s-1"])


def test_other_workspace_has_no_sync_dir(cases_root):
    r = resolve_path("finance@canonical/statement-analysis", cases_root, "akshat")
    assert r.ref == Reference("finance", "canonical")
    assert r.dataset_dir is None and r.selection == Selection(tag="statement-analysis")
    r = resolve_path("other@ravi/sub", cases_root, "akshat")
    assert r.selection == Selection(tag="sub")
    with pytest.raises(ValueError):
        resolve_path("other@ravi/sub/x.yaml", cases_root, "akshat")


def test_own_workspace_needs_directory_and_config(cases_root):
    with pytest.raises(ValueError, match="no dataset directory"):
        resolve_path("missing", cases_root, "akshat")
    with pytest.raises(ValueError, match="evalkit.yaml"):
        resolve_path("missing-name", None, "akshat")
    r = resolve_path("finance@canonical", None, "akshat")
    assert r.dataset_dir is None


def test_sync_files_keeps_relative_names_and_registry(cases_root):
    finance = cases_root / "finance"
    names = [name for name, _ in sync_files(finance)]
    assert names == ["mf.yaml", "statement-analysis/meridian.yaml", "fixtures.yaml"]
    names = [name for name, _ in sync_files(finance, "mf.yaml")]
    assert names == ["mf.yaml", "fixtures.yaml"]
    names = [name for name, _ in sync_files(finance, "statement-analysis")]
    assert names == ["statement-analysis/meridian.yaml", "fixtures.yaml"]
    names = [name for name, _ in sync_files(finance, "statement-analysis/meridian.yaml")]
    assert names == ["statement-analysis/meridian.yaml", "fixtures.yaml"]


def test_git_revision(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )
    rev = git_revision(repo)
    assert rev.branch == "main" and rev.dirty is False
    assert rev.sha is not None and len(rev.sha) == 40
    (repo / "a.txt").write_text("b")
    assert git_revision(repo).dirty is True
    assert git_revision(Path("/")).sha is None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _StubClient:
    def __init__(self, known: set[str]):
        self.known = known
        self.puts: list[tuple[str, bytes]] = []
        self.heads: list[str] = []

    def head(self, path):
        class R:
            def __init__(self, status):
                self.status_code = status

        self.heads.append(path)
        return R(200 if path.removeprefix("/api/fixtures/sha256/") in self.known else 404)

    def put(self, path, content=b"", retry=None):
        self.puts.append((path, content))


def _registry(tmp_path, entries: dict[str, bytes]) -> Path:
    registry = tmp_path / "fixtures.yaml"
    registry.write_text(
        "fixtures:\n"
        + "".join(
            f"- key: {key}\n  sha256: {_sha(data)}\n  context: c\n" for key, data in entries.items()
        )
    )
    return registry


def test_upload_fixtures_sends_only_bytes_the_server_lacks(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "same.csv").write_bytes(b"same")
    (fixtures / "new.csv").write_bytes(b"new")
    registry = _registry(tmp_path, {"same.csv": b"same", "new.csv": b"new"})
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  fixtures: [same.csv, new.csv]\n  input: {question: q}\n")
    client = _StubClient({_sha(b"same")})
    uploaded = upload_fixtures(client, [("c.yaml", case), ("fixtures.yaml", registry)], fixtures)
    assert uploaded == ["new.csv"]
    assert client.puts == [(f"/api/fixtures/sha256/{_sha(b'new')}", b"new")]


def test_upload_fixtures_shows_the_size_limit_the_server_hit(tmp_path, capsys):
    detail = (
        "fixture upload is larger than the 500 MB limit (server config limits.fixture_upload_bytes)"
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(413, json={"detail": detail})
        return httpx.Response(404)

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "big.csv").write_bytes(b"big")
    registry = _registry(tmp_path, {"big.csv": b"big"})
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  fixtures: [big.csv]\n  input: {question: q}\n")
    client = ApiClient("http://test", transport=httpx.MockTransport(handle), check_version=False)
    with pytest.raises(typer.Exit):
        upload_fixtures(client, [("c.yaml", case), ("fixtures.yaml", registry)], fixtures)
    assert f"→ 413: {detail}" in capsys.readouterr().err


def test_upload_fixtures_skips_a_missing_local_file_the_server_already_has(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    registry = _registry(tmp_path, {"shared.csv": b"canonical bytes"})
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  fixtures: [shared.csv]\n  input: {question: q}\n")
    client = _StubClient({_sha(b"canonical bytes")})
    files = [("c.yaml", case), ("fixtures.yaml", registry)]
    assert upload_fixtures(client, files, fixtures) == []
    assert client.puts == []
    assert client.heads == [f"/api/fixtures/sha256/{_sha(b'canonical bytes')}"]


def test_upload_fixtures_rejects_missing_or_mismatched_files(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    registry = tmp_path / "fixtures.yaml"
    registry.write_text(f"fixtures:\n- key: gone.csv\n  sha256: {'a' * 64}\n  context: g\n")
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  fixtures: [gone.csv]\n  input: {question: q}\n")
    files = [("c.yaml", case), ("fixtures.yaml", registry)]
    with pytest.raises(ValueError, match="does not exist and the server has no bytes"):
        upload_fixtures(_StubClient(set()), files, fixtures)
    (fixtures / "gone.csv").write_bytes(b"other")
    with pytest.raises(ValueError, match="update the registry"):
        upload_fixtures(_StubClient(set()), files, fixtures)


def test_git_revision_without_git_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert git_revision(tmp_path) == GitRevision()
