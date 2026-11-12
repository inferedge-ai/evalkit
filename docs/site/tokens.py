import re
from pathlib import Path

TOKEN_FILES = ("semantic.css", "brand.css")
COMMENT = re.compile(r"/\*.*?\*/", re.S)
BLOCK = re.compile(r"(:root|\.dark)\s*\{(.*?)\}", re.S)
THEME_ROLES = ("--color-ok", "--color-warn")
THEME_BLOCK = re.compile(r"(@theme|\.dark)\s*\{(.*?)\n\}", re.S)
DARK_SCHEME = '[data-md-color-scheme="slate"]'

# Tailwind v4's gray and white, which the UI tokens reference; a test checks them
# against ui/node_modules/tailwindcss/theme.css.
TAILWIND_COLORS = {
    "--color-white": "#fff",
    "--color-gray-50": "oklch(98.5% 0.002 247.839)",
    "--color-gray-100": "oklch(96.7% 0.003 264.542)",
    "--color-gray-200": "oklch(92.8% 0.006 264.531)",
    "--color-gray-300": "oklch(87.2% 0.01 258.338)",
    "--color-gray-400": "oklch(70.7% 0.022 261.325)",
    "--color-gray-500": "oklch(55.1% 0.027 264.364)",
    "--color-gray-600": "oklch(44.6% 0.03 256.802)",
    "--color-gray-700": "oklch(37.3% 0.034 259.733)",
    "--color-gray-800": "oklch(27.8% 0.033 256.848)",
    "--color-gray-900": "oklch(21% 0.034 264.665)",
    "--color-gray-950": "oklch(13% 0.028 261.692)",
}


def read_token_blocks(tokens_dir: Path) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {":root": [], ".dark": []}
    for name in TOKEN_FILES:
        source = COMMENT.sub("", (tokens_dir / name).read_text())
        for selector, body in BLOCK.findall(source):
            blocks[selector].extend(
                " ".join(line.split()) for line in body.split(";") if line.strip()
            )
    return blocks


def read_theme_roles(theme_css: Path) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {":root": [], ".dark": []}
    source = COMMENT.sub("", theme_css.read_text())
    for selector, body in THEME_BLOCK.findall(source):
        for line in body.split(";"):
            declaration = " ".join(line.split())
            if declaration.split(":")[0] in THEME_ROLES:
                roles[":root" if selector == "@theme" else ".dark"].append(declaration)
    return roles


def render_site_tokens(tokens_dir: Path) -> str:
    blocks = read_token_blocks(tokens_dir)
    for selector, lines in read_theme_roles(tokens_dir.parent.parent / "theme.css").items():
        blocks[selector].extend(lines)
    palette = [f"{name}: {value}" for name, value in TAILWIND_COLORS.items()]
    light = "".join(f"  {line};\n" for line in palette + blocks[":root"])
    dark = "".join(f"  {line};\n" for line in blocks[".dark"])
    return f":root {{\n{light}}}\n\n{DARK_SCHEME} {{\n{dark}}}\n"
