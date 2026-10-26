import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
USER_DOCS = ROOT / "docs" / "user"


def test_user_pages_link_only_to_existing_user_pages():
    pages = {p.name for p in USER_DOCS.glob("*.md")}
    for page in USER_DOCS.glob("*.md"):
        for href in re.findall(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)", page.read_text()):
            assert href in pages, f"{page.name} links {href}"
