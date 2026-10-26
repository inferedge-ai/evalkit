from pathlib import Path

from evalkit.core.blob import fixture_blob_key
from evalkit.core.fixtures import compose_input, load_registries, sha256_hex
from evalkit.store.base import CaseFile

D1 = sha256_hex(b"one")
D2 = sha256_hex(b"two")


def write_registry(path: Path, *entries: tuple[str, str, str]) -> Path:
    lines = ["fixtures:"]
    for key, digest, context in entries:
        lines += [f"- key: {key}", f"  sha256: {digest}", f"  context: {context}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_fixture_blob_key_is_content_addressed():
    assert fixture_blob_key(D1) == f"fixtures/sha256/{D1}"


def test_load_registries_unions_and_flags_duplicates(tmp_path):
    a = write_registry(tmp_path / "a.yaml", ("one.pdf", D1, "first"), ("shared.pdf", D1, "x"))
    b = write_registry(tmp_path / "b.yaml", ("two.pdf", D2, "second"), ("shared.pdf", D1, "x"))
    entries, findings = load_registries([a, b, tmp_path / "missing.yaml"])
    assert set(entries) == {"one.pdf", "two.pdf", "shared.pdf"}
    assert [(f.code, f.case_id) for f in findings] == [("E-FIXTURE-DUPLICATE-KEY", "shared.pdf")]


def test_load_registries_reports_schema_errors(tmp_path):
    bad = tmp_path / "fixtures.yaml"
    bad.write_text("fixtures:\n- key: a/b.pdf\n  sha256: nope\n  context: x\n")
    entries, findings = load_registries([bad])
    assert entries == {}
    assert findings[0].code == "E-SCHEMA" and findings[0].case_id == "fixtures.yaml"


def test_compose_input_prepends_file_block():
    composed = compose_input(
        {"question": "Compute net debt.", "locale": "en"},
        [
            CaseFile(key="bs.csv", sha256=D1, context="Balance sheet, USD thousands."),
            CaseFile(key="cf.csv", sha256=D2, context="Cash flow statement."),
        ],
    )
    assert composed == {
        "question": (
            "Attached files:\n"
            "- bs.csv: Balance sheet, USD thousands.\n"
            "- cf.csv: Cash flow statement.\n"
            "\n"
            "Compute net debt."
        ),
        "locale": "en",
    }


def test_compose_input_without_files_is_unchanged():
    assert compose_input({"question": "q"}, []) == {"question": "q"}


def test_compose_input_does_not_compose_twice():
    question = "Attached files:\n- bs.csv: Balance sheet.\n\nCompute net debt."
    files = [CaseFile(key="bs.csv", sha256=D1, context="Balance sheet.")]
    assert compose_input({"question": question}, files) == {"question": question}
