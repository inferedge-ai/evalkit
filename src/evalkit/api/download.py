import re
from urllib.parse import quote

# Agent-controlled filenames flow into Content-Disposition; Starlette encodes
# headers as latin-1, so anything outside this set goes into filename* only.
_ASCII_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def content_disposition(name: str) -> dict[str, str]:
    ascii_name = _ASCII_SAFE.sub("_", name).strip("._") or "file"
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
        )
    }
