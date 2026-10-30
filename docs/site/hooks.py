import os
import re
import tomllib
from pathlib import Path

from mkdocs.structure.files import File, Files

SITE = Path(__file__).parent
ROOT = SITE.parent.parent
GENERATED = {
    "sdk-reference.md": SITE / "sdk-reference.md",
    "stylesheets/evalkit.css": SITE / "stylesheets" / "evalkit.css",
    "favicon.svg": ROOT / "ui" / "public" / "favicon.svg",
}
PLACEHOLDER = re.compile(r"\{\{(origin|version)\}\}")
REPO_LINK = re.compile(r"\]\((?!https?:|mailto:|#)([^)]+)\)")
REPO_BLOB = "https://github.com/inferedge-ai/evalkit/blob/"
GITHUB_ONLY = "On GitHub, `{{origin}}` in the docs stands for your server's URL.\n"


def _version() -> str:
    return os.environ.get("DOCS_VERSION") or tomllib.loads(
        (ROOT / "pyproject.toml").read_text()
    )["project"]["version"]


def _site_link(target: str) -> str:
    if target.startswith("docs/user/"):
        return target.removeprefix("docs/user/")
    return f"{REPO_BLOB}{os.environ.get('DOCS_REF', 'develop')}/{target}"


def _home_page() -> str:
    readme = (ROOT / "README.md").read_text().replace(GITHUB_ONLY, "")
    return REPO_LINK.sub(lambda match: f"]({_site_link(match.group(1))})", readme)


def on_files(files: Files, config) -> Files:
    for name, source in GENERATED.items():
        files.append(File.generated(config, name, abs_src_path=str(source)))
    files.append(File.generated(config, "index.md", content=_home_page()))
    return files


def on_page_markdown(markdown: str, page, config, files) -> str:
    if page.file.src_uri == "index.md":
        return markdown
    values = {"origin": "http://localhost:8020", "version": _version()}
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], markdown)
