from evalkit.client.sync import sync_folder


def test_sync_folder_normalizes_the_subfolder(tmp_path):
    (tmp_path / "tax" / "vat").mkdir(parents=True)
    (tmp_path / "tax" / "case.yaml").write_text("")

    assert sync_folder(tmp_path, "tax/../tax/vat") == "tax/vat"
    assert sync_folder(tmp_path, "tax/") == "tax"
    assert sync_folder(tmp_path, "tax/case.yaml") is None
    assert sync_folder(tmp_path, ".") is None
    assert sync_folder(tmp_path, None) is None
