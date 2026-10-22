"""Build ``docs/index.html`` from the README.

The `docs` extra installs ``markdown_it`` and ``pygments``::

    python tools/build_docs.py

The page is self-contained — the screenshot is inlined — so it can be opened
from the filesystem or dropped on any static host as-is.
"""

from __future__ import annotations

import base64
import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "README.md"
OUTPUT = ROOT / "docs" / "index.html"
SCREENSHOT = ROOT / "docs" / "screenshot.svg"

#: ``shell``, ``bash``, ``sh`` all mean the same thing to Pygments.
_ALIASES = {"sh": "bash", "console": "shell", "text": "text"}

TITLE = "scriptsuite"
SUBTITLE = "A modern terminal workspace for driving test scripts"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _lexer(name: str, code: str):
    """Best Pygments lexer for a fenced block."""
    name = _ALIASES.get(name.strip().lower(), name.strip())
    if name:
        try:
            return get_lexer_by_name(name, stripall=False)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return get_lexer_by_name("text")


def highlight_code(code: str, language: str = "") -> str:
    lexer = _lexer(language, code)
    return highlight(code, lexer, HtmlFormatter(nowrap=True))


def slugify(text: str) -> str:
    """A URL fragment for a heading."""
    plain = re.sub(r"<[^>]+>", "", text)
    plain = re.sub(r"[^\w\s-]", "", plain).strip().lower()
    return re.sub(r"[-\s]+", "-", plain) or "section"


def add_heading_ids(md: MarkdownIt) -> None:
    """Give every ``<h2>`` an id, so the contents box can link to it."""

    # markdown-it keeps its renderer rules in a plain dict and calls them
    # unbound, so this takes no `self`.
    def _heading_open(tokens, idx, options, env, renderer=None):  # noqa: ANN001
        token = tokens[idx]
        if token.tag == "h2" and idx + 1 < len(tokens):
            token.attrSet("id", slugify(tokens[idx + 1].content))
        return md.renderer.renderToken(tokens, idx, options, env)

    md.renderer.rules["heading_open"] = _heading_open


def render(md: MarkdownIt, text: str) -> str:
    """Markdown -> HTML, with pygments styling applied to fenced code."""
    tokens = md.parse(text)
    for token in tokens:
        if token.type != "fence":
            continue
        language = (token.info or "").strip()
        # Mutate in place; the renderer walks this same list.
        token.type = "html_block"
        token.content = (
            '<div class="highlight"><pre><code>'
            f"{highlight_code(token.content, language)}"
            "</code></pre></div>"
        )
    return md.renderer.render(tokens, md.options, {})


# ---------------------------------------------------------------------------
# Inlining
# ---------------------------------------------------------------------------


def inline_screenshot(markdown: str) -> str:
    """Replace the screenshot reference with an inline copy of the SVG.

    Inlining keeps the page a single file that works offline, which a bare
    ``<img src="screenshot.svg">`` would not.
    """
    if not SCREENSHOT.exists():
        print(f"warning: {SCREENSHOT} missing; run tools/screenshot.py", file=sys.stderr)
        return markdown
    encoded = base64.b64encode(SCREENSHOT.read_bytes()).decode("ascii")
    return re.sub(
        r"!\[(?P<alt>[^\]]*)\]\((?P<src>docs/screenshot\.svg)\)",
        lambda m: (
            f'<img alt="{m.group("alt")}" src="data:image/svg+xml;base64,{encoded}" />'
        ),
        markdown,
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfd;
  --panel: #ffffff;
  --ink: #1f2330;
  --muted: #5d6478;
  --line: #e3e6ef;
  --accent: #3b5bdb;
  --accent-soft: #edf1ff;
  --code-bg: #f5f6fa;
  --shadow: 0 1px 2px rgba(20, 24, 40, .06), 0 8px 24px rgba(20, 24, 40, .06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #11111b;
    --panel: #1a1b26;
    --ink: #c0caf5;
    --muted: #8b93b0;
    --line: #2a2d3f;
    --accent: #7aa2f7;
    --accent-soft: #1c2340;
    --code-bg: #16161e;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 10px 30px rgba(0, 0, 0, .35);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
body {
  margin: 0;
  padding: 0 1.25rem 6rem;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.65 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  font-feature-settings: "kern";
}
.wrap { max-width: 62rem; margin: 0 auto; }
header.masthead {
  padding: 3.5rem 0 2rem;
  border-bottom: 1px solid var(--line);
  margin-bottom: 2rem;
}
header.masthead h1 {
  margin: 0;
  font-size: clamp(2rem, 5vw, 2.75rem);
  letter-spacing: -.02em;
  line-height: 1.1;
}
header.masthead h1 code {
  font-size: inherit;
  background: none;
  padding: 0;
  border: 0;
  color: inherit;
}
header.masthead p { margin: .6rem 0 0; color: var(--muted); font-size: 1.05rem; }
nav.toc {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 1.25rem 1.5rem;
  margin: 2rem 0;
  box-shadow: var(--shadow);
}
nav.toc h2 { margin: 0 0 .6rem; font-size: .8rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
nav.toc ol { margin: 0; padding-left: 1.1rem; columns: 2; column-gap: 2rem; }
nav.toc li { margin: .2rem 0; break-inside: avoid; }
@media (max-width: 40rem) { nav.toc ol { columns: 1; } }
main {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: clamp(1.25rem, 3vw, 2.75rem);
  box-shadow: var(--shadow);
}
h2 {
  margin: 2.75rem 0 1rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--line);
  font-size: 1.5rem;
  letter-spacing: -.01em;
}
main > h2:first-of-type { margin-top: 0; padding-top: 0; border-top: 0; }
h3 { margin: 2rem 0 .75rem; font-size: 1.15rem; letter-spacing: -.01em; }
a { color: var(--accent); text-decoration-color: color-mix(in oklab, var(--accent) 40%, transparent); text-underline-offset: 2px; }
a:hover { text-decoration-color: var(--accent); }
p, li { color: var(--ink); }
strong { font-weight: 650; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2.5rem 0; }
code {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
               "Liberation Mono", monospace;
  font-size: .875em;
  background: var(--code-bg);
  padding: .16em .38em;
  border-radius: 5px;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 1rem 1.15rem;
  overflow-x: auto;
  font-size: .84rem;
  line-height: 1.6;
}
pre code { background: none; padding: 0; font-size: inherit; }
table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: .94rem; }
th, td { text-align: left; padding: .55rem .7rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; }
tbody tr:last-child td { border-bottom: 0; }
img { max-width: 100%; height: auto; display: block; margin: 1.75rem 0; border-radius: 12px; border: 1px solid var(--line); background: #11111b; }
blockquote {
  margin: 1.5rem 0;
  padding: .75rem 1.15rem;
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 0 10px 10px 0;
}
blockquote p { margin: .35rem 0; }
footer { margin-top: 2.5rem; color: var(--muted); font-size: .875rem; text-align: center; }
footer a { color: var(--muted); }
/* Pygments, mapped onto the palette so code follows the light/dark theme. */
.highlight { background: transparent; }
.codehilite { background: transparent; }
.highlight .hll, .codehilite .hll { background: var(--accent-soft); display: block; }
.highlight pre, .codehilite pre { margin: 0; border: 0; background: transparent; }
@media (prefers-color-scheme: dark) {
  .highlight .c, .codehilite .c { color: #5c6480; font-style: italic; }
  .highlight .k, .codehilite .k { color: #bb9af7; }
  .highlight .s, .codehilite .s { color: #9ece6a; }
  .highlight .n, .codehilite .n { color: #c0caf5; }
  .highlight .nf, .codehilite .nf { color: #7aa2f7; }
  .highlight .nc, .codehilite .nc { color: #2ac3de; }
  .highlight .mi, .codehilite .mi { color: #ff9e64; }
  .highlight .o, .codehilite .o { color: #89ddff; }
  .highlight .p, .codehilite .p { color: #89ddff; }
  .highlight .err, .codehilite .err { color: #f7768e; }
}
@media print {
  body { background: #fff; padding: 0; }
  nav.toc { box-shadow: none; }
  main { box-shadow: none; border: 0; }
}
"""

#: The README keeps a plain ASCII sketch for the terminal; on the web it is
#: clearer to show the real screenshot, so that block is dropped.
ASCII_SKETCH = re.compile(r"```\n┌.*?└.*?```\n", re.DOTALL)

#: The README carries its own contents list for markdown viewers that do not
#: build one.  On the web it is replaced by the generated box, so it is
#: removed here rather than shown twice.
MANUAL_TOC = re.compile(r"^##\s+Contents\s*\n(?:(?!^##\s).*\n)*", re.MULTILINE)

#: The nav never lists itself.
SELF = "contents"

#: The page supplies its own masthead, so the README's centred title block and
#: badge line are dropped rather than shown twice.
MASTHEAD_BLOCKS = re.compile(
    r"^<h1[^>]*>.*?</h1>\s*\n"  # centred title
    r"|<p align=\"center\"><em>.*?</em></p>\s*\n"  # centred subtitle
    r"|<p align=\"center\">\s*<sub>.*?</sub>\s*</p>\s*\n",  # centred badge
    re.MULTILINE | re.DOTALL,
)


def build_markdown(markdown: str) -> str:
    markdown = inline_screenshot(markdown)
    markdown = ASCII_SKETCH.sub("", markdown, count=1)
    markdown = MANUAL_TOC.sub("", markdown, count=1)
    markdown = MASTHEAD_BLOCKS.sub("", markdown)
    # A rule straight before the first heading adds nothing under the nav box.
    markdown = re.sub(r"\n---\n+(?=## )", "\n\n", markdown, count=1)
    return markdown.lstrip("\n")


def page(body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE} — {SUBTITLE}</title>
<meta name="description" content="{SUBTITLE}.">
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
<header class="masthead">
  <h1>{TITLE}</h1>
  <p>{SUBTITLE}</p>
</header>
{body}
<footer>
  Generated from <code>README.md</code> with <code>tools/build_docs.py</code>.
</footer>
</div>
</body>
</html>
"""


def main() -> int:
    markdown = SOURCE.read_text(encoding="utf-8")

    md = MarkdownIt("commonmark", {"html": True, "linkify": True})
    md.enable("table")
    md.enable("strikethrough")
    add_heading_ids(md)

    article = render(md, build_markdown(markdown))

    # A contents block, built from the headings the render produced.
    toc = extract_toc(article)
    body = f"{toc}\n<main>{article}</main>" if toc else f"<main>{article}</main>"

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page(body), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}  ({OUTPUT.stat().st_size:,} bytes)")
    return 0


def extract_toc(body: str) -> str:
    """Pull the ``<h2>`` headings out of the rendered body for a contents box."""
    items = re.findall(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', body, re.DOTALL)
    if not items:
        return ""
    entries = "".join(
        f'<li><a href="#{html.escape(anchor)}">{re.sub(r"<[^>]+>", "", text)}</a></li>'
        for anchor, text in items
        if anchor != SELF
    )
    return f'<nav class="toc"><h2>Contents</h2><ol>{entries}</ol></nav>'


if __name__ == "__main__":
    raise SystemExit(main())
