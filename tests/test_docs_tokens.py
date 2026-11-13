import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("docs_tokens", ROOT / "docs" / "site" / "tokens.py")
assert _spec and _spec.loader
tokens = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tokens)

TOKENS_DIR = ROOT / "ui" / "src" / "styles" / "tokens"


def test_every_tailwind_colour_the_tokens_use_is_defined():
    css = tokens.render_site_tokens(TOKENS_DIR)
    used = set(re.findall(r"var\((--color-[\w-]+)\)", css))
    assert used <= set(tokens.TAILWIND_COLORS)


def test_every_declared_token_reaches_the_site():
    css = tokens.render_site_tokens(TOKENS_DIR)
    light, dark = css.split(tokens.DARK_SCHEME)
    for name in tokens.TOKEN_FILES:
        source = re.sub(r"/\*.*?\*/", "", (TOKENS_DIR / name).read_text(), flags=re.S)
        for selector, block in (
            (":root", light),
            (".dark", dark),
        ):
            start = source.find(f"{selector} {{")
            if start == -1:
                continue
            body = source[start : source.index("\n}", start)]
            for prop in re.findall(r"^\s*(--[\w-]+)\s*:", body, flags=re.M):
                assert f"  {prop}:" in block, (name, selector, prop)
