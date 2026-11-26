import pytest

from evalkit.client.sync import resolve_subfolder, sync_files


def test_resolve_subfolder_normalizes_the_path_inside_the_dataset(tmp_path):
    (tmp_path / "tax" / "vat").mkdir(parents=True)
    (tmp_path / "tax" / "case.yaml").write_text("")

    assert resolve_subfolder(tmp_path, "tax/../tax/vat") == "tax/vat"
    assert resolve_subfolder(tmp_path, "tax/") == "tax"
    assert resolve_subfolder(tmp_path, "tax/case.yaml") is None
    assert resolve_subfolder(tmp_path, ".") is None
    assert resolve_subfolder(tmp_path, None) is None
    with pytest.raises(ValueError, match="outside"):
        resolve_subfolder(tmp_path / "tax", "../tax")


def test_resolve_subfolder_keeps_a_symlinked_folder(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "case.yaml").write_text("cases: []\n")
    dataset = tmp_path / "finance"
    dataset.mkdir()
    (dataset / "tax").symlink_to(outside)

    assert resolve_subfolder(dataset, "tax") == "tax"
    assert [name for name, _ in sync_files(dataset, "tax")] == ["tax/case.yaml"]
