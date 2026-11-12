import re

TABS_OPEN = "<!-- tabs -->"
TABS_CLOSE = "<!-- /tabs -->"
ANCHOR_ROWS = "<!-- anchor-rows -->"
TAB_HEADING = re.compile(r"^#### (.+?)\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]+>")
NOT_SLUG = re.compile(r"[^a-z0-9]+")
TABLE_DIVIDER = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def slugify_row(text: str) -> str:
    return NOT_SLUG.sub("-", text.lower()).strip("-")


def _toggle_fence(line: str, fence: str | None) -> str | None:
    match = FENCE.match(line)
    if not match:
        return fence
    if fence is None:
        return match.group(1)
    return None if match.group(1).startswith(fence) else fence


def convert_tabs(markdown: str) -> str:
    out: list[str] = []
    in_tabs = False
    fence: str | None = None
    for line in markdown.split("\n"):
        if fence is None and line.strip() == TABS_OPEN:
            in_tabs = True
            continue
        if fence is None and in_tabs and line.strip() == TABS_CLOSE:
            in_tabs = False
            out.append("")
            continue
        heading = TAB_HEADING.match(line) if in_tabs and fence is None else None
        if heading:
            label = heading.group(1).replace('"', "&quot;")
            out.extend(["", f'=== "{label}"', ""])
            continue
        if in_tabs:
            fence = _toggle_fence(line, fence)
            out.append(f"    {line}" if line.strip() else "")
        else:
            fence = _toggle_fence(line, fence)
            out.append(line)
    if in_tabs:
        raise ValueError(f"{TABS_OPEN} without {TABS_CLOSE}")
    return "\n".join(out)


def _first_cell(row: str) -> str:
    return row.strip().removeprefix("|").split("|", 1)[0]


def _read_cell_text(cell: str) -> str:
    return MARKDOWN_LINK.sub(r"\1", HTML_TAG.sub("", cell))


def anchor_rows(markdown: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    fence: str | None = None
    while index < len(lines):
        line = lines[index]
        fence = _toggle_fence(line, fence)
        if fence is not None or line.strip() != ANCHOR_ROWS:
            out.append(line)
            index += 1
            continue
        out.extend(lines[index + 1 : index + 3])
        if len(lines) < index + 3 or not TABLE_DIVIDER.match(lines[index + 2]):
            raise ValueError(f"{ANCHOR_ROWS} must sit right before a table")
        index += 3
        seen: set[str] = set()
        while index < len(lines) and lines[index].strip().startswith("|"):
            row = lines[index]
            slug = slugify_row(_read_cell_text(_first_cell(row)))
            if not slug or slug in seen:
                raise ValueError(f"{ANCHOR_ROWS}: empty or repeated row id {slug!r} in {row!r}")
            seen.add(slug)
            lead, rest = row.split("|", 1)
            out.append(f'{lead}| <span id="{slug}" class="ek-row-anchor"></span>{rest}')
            index += 1
    return "\n".join(out)
