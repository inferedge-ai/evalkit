import os
import re
import tomllib
from pathlib import Path

from mkdocs.structure.files import File, Files

SITE = Path(__file__).parent
ROOT = SITE.parent.parent
GENERATED = ("sdk-reference.md", "stylesheets/evalkit.css", "favicon.svg")
PLACEHOLDER = re.compile(r"\{\{(origin|version)\}\}")


def _version() -> str:
    return os.environ.get("DOCS_VERSION") or tomllib.loads(
        (ROOT / "pyproject.toml").read_text()
    )["project"]["version"]


def on_files(files: Files, config) -> Files:
    for name in GENERATED:
        files.append(File.generated(config, name, abs_src_path=str(SITE / name)))
    return files


def on_page_markdown(markdown: str, page, config, files) -> str:
    values = {"origin": "http://localhost:8020", "version": _version()}
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], markdown)
