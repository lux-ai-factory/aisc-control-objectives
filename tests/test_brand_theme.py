"""The pages wear the AISC brand theme: the :root colours of shared/theme/brand.json,
Raleway for text, MuseoModerno for headings, IBM Plex Mono for ids and code. Inter is gone."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "src" / "aisc_control_objectives" / "templates" / "_base.html.j2"
SHARED = ROOT.parents[1] / "shared" / "theme" / "brand.json"

# brand.json's values, kept here too so the suite holds the app to them outside the platform checkout
BRAND = {
    "--bg": "#f2f3fd", "--panel": "#ffffff", "--panel-2": "#f2f3fd", "--text": "#0b0b2e",
    "--muted": "#4a4f6a", "--faint": "#6b7090", "--primary": "#000fdf", "--primary-hover": "#000079",
    "--accent": "#ff007e", "--accent-hover": "#cc0066", "--info": "#0099ff", "--info-soft": "#ccebff",
    "--pink-soft": "#ffcce5", "--border": "#cccff9", "--border-strong": "#999ff2", "--error": "#e30613",
    "--heading": "#000079",
}


def _css() -> str:
    return BASE.read_text()


def _root_colours() -> dict[str, str]:
    found = {}
    for block in re.findall(r":root\s*\{([^}]*)\}", _css()):
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", block):
            found[name] = value
    return found


def test_the_copy_here_matches_brand_json():
    if SHARED.exists():
        assert json.loads(SHARED.read_text())["variables"] == BRAND


def test_every_colour_variable_is_the_brand_value():
    colours = _root_colours()
    assert colours, "no colour variables in :root"
    for name, value in colours.items():
        assert name in BRAND, f"{name} is not a brand variable"
        assert value == BRAND[name], f"{name} is {value}, brand says {BRAND[name]}"
    assert "--heading" in colours


def test_the_brand_fonts_are_loaded_and_inter_is_gone():
    css = _css()
    assert "Inter" not in css
    link = re.search(r'href="(https://fonts\.googleapis\.com/css2\?[^"]+)"', css).group(1)
    assert "family=MuseoModerno:wght@500;600;700" in link
    assert "family=Raleway:wght@400;500;600;700" in link
    assert "family=IBM+Plex+Mono:wght@400;500;600" in link


def test_text_is_raleway_headings_museomoderno_ids_plex_mono():
    css = _css()
    assert re.search(r':root\s*\{[^}]*font-family:\s*"Raleway"', css)
    heading = re.search(r"h1, h2, h3, h4\s*\{([^}]*)\}", css).group(1)
    assert '"MuseoModerno"' in heading and "color: var(--heading)" in heading
    for rule in (".qf-dl code", ".co-obj-id", ".co-macro-id", ".co-code"):
        body = re.search(re.escape(rule) + r"\s*\{([^}]*)\}", css).group(1)
        assert '"IBM Plex Mono"' in body, rule


def _rule(selector: str) -> str:
    match = re.search(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", _css())
    assert match, f"no rule {selector}"
    return match.group(1)


def test_the_site_header_is_the_brand_header():
    """shared/theme/brand.json "header", the same in every app: 89px tall (20px + 48px logo + 20px and
    the 1px border), 40px at the sides, the product title 19px MuseoModerno 700 as written."""
    assert re.search(r"--site-header-h:\s*89px;", _css())
    assert "border-bottom: 1px solid var(--border);" in _rule(".site-header")
    assert "padding: 20px 40px;" in _rule(".site-header .inner")
    assert re.search(r"height:\s*48px;", _rule(".site-header .brand img"))
    brand = _rule(".site-header .brand")
    assert '"MuseoModerno"' in brand
    assert "font-size: 19px;" in brand and "font-weight: 700;" in brand
    assert "letter-spacing" not in brand and "text-transform" not in brand
