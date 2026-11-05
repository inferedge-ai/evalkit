from evalkit import paths


def test_ui_root_prefers_a_checkout_build(tmp_path, monkeypatch):
    (tmp_path / "ui" / "dist").mkdir(parents=True)
    monkeypatch.setattr(paths, "_REPO", tmp_path)
    assert paths.ui_root() == tmp_path / "ui" / "dist"


def test_ui_root_falls_back_to_the_bundled_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_REPO", tmp_path)
    assert paths.ui_root() == paths._PACKAGE / "bundled" / "ui"
