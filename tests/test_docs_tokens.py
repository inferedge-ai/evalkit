import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("docs_tokens", ROOT / "docs" / "site" / "tokens.py")
assert _spec and _spec.loader
tokens = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tokens)

TOKENS_DIR = ROOT / "ui" / "src" / "styles" / "tokens"
TAILWIND_THEME = ROOT / "ui" / "node_modules" / "tailwindcss" / "theme.css"


def test_site_tokens_carry_the_ui_tokens_for_both_schemes():
    css = tokens.render_site_tokens(TOKENS_DIR)
    light, dark = css.split(tokens.DARK_SCHEME)
    assert "--primary: oklch(0.5299 0.1627 260.34);" in light
    assert "--background: var(--color-white);" in light
    assert "--color-gray-900: oklch(21% 0.034 264.665);" in light
    assert "--primary: oklch(0.7526 0.1278 258.16);" in dark
    assert "--background: var(--color-gray-950);" in dark


def test_every_tailwind_colour_the_tokens_use_is_defined():
    css = tokens.render_site_tokens(TOKENS_DIR)
    used = set(re.findall(r"var\((--color-[\w-]+)\)", css))
    assert used <= set(tokens.TAILWIND_COLORS)


def test_tailwind_colours_match_the_installed_tailwind():
    if not TAILWIND_THEME.is_file():
        pytest.skip("ui/node_modules is not installed")
    theme = TAILWIND_THEME.read_text()
    for name, value in tokens.TAILWIND_COLORS.items():
        assert f"{name}: {value};" in theme, name
