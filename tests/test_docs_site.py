import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _ui_pages() -> list[str]:
    source = (ROOT / "ui" / "src" / "lib" / "docs.ts").read_text()
    files = dict(
        re.findall(r'import (\w+) from "\.\./\.\./\.\./docs/user/([\w-]+\.md)\?raw"', source)
    )
    tabs = source[source.index("DOC_TABS") :]
    return [files[name] for name in re.findall(r"body: (\w+)", tabs)]


def _nav_pages(nav: list) -> list[str]:
    pages = []
    for item in nav:
        value = next(iter(item.values())) if isinstance(item, dict) else item
        pages.extend(_nav_pages(value) if isinstance(value, list) else [value])
    return pages


def test_site_nav_lists_the_ui_docs_pages_in_order():
    config = yaml.load((ROOT / "mkdocs.yml").read_text(), Loader=_Loader)
    assert config["docs_dir"] == "docs/user"
    pages = _nav_pages(config["nav"])
    site_only = {"index.md", "sdk-reference.md"}
    assert [page for page in pages if page not in site_only] == _ui_pages()
    assert site_only <= set(pages)
