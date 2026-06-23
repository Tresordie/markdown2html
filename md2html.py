#!/usr/bin/env python3
"""Convert a Markdown file to a standalone styled HTML page with Mermaid support."""

import re
import sys
from pathlib import Path
from typing import Optional

try:
    import markdown
except ImportError:
    sys.exit("请先安装 markdown 库: pip install markdown pymdown-extensions")


CSS = """
:root {
    --bg: #ffffff; --fg: #24292f; --border: #d0d7de; --muted: #656d76;
    --accent: #0969da; --code-bg: #f6f8fa; --radius: 6px;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #0d1117; --fg: #c9d1d9; --border: #30363d; --muted: #8b949e;
        --accent: #58a6ff; --code-bg: #161b22;
    }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 16px; line-height: 1.6; color: var(--fg); background: var(--bg);
    max-width: 900px; margin: 0 auto; padding: 40px 20px;
}
h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom: .3em; margin: 24px 0 16px; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom: .3em; margin: 24px 0 16px; }
h3 { font-size: 1.25em; margin: 24px 0 16px; }
p { margin-bottom: 16px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 85%; }
:not(pre) > code {
    background: var(--code-bg); padding: .2em .4em; border-radius: var(--radius);
}
pre {
    background: var(--code-bg); border-radius: var(--radius); padding: 16px;
    overflow-x: auto; margin-bottom: 16px; line-height: 1.45;
}
blockquote {
    margin: 0 0 16px; padding: 0 1em; color: var(--muted); border-left: .25em solid var(--border);
}
table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
th, td { padding: 8px 13px; border: 1px solid var(--border); text-align: left; }
th { background: var(--code-bg); font-weight: 600; }
tr:nth-child(even) { background: var(--code-bg); }
img { max-width: 100%; border-radius: var(--radius); }
ul, ol { padding-left: 2em; margin-bottom: 16px; }
li { margin-bottom: 4px; }
li > input[type="checkbox"] { margin-right: 6px; }
hr { border: 0; border-top: 1px solid var(--border); margin: 24px 0; }
details { margin-bottom: 16px; border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; }
summary { cursor: pointer; font-weight: 600; }
.mermaid { text-align: center; margin: 16px 0; }
@media print {
    body { max-width: none; padding: 0; font-size: 12px; }
    pre, blockquote, table, img { page-break-inside: avoid; }
}
"""

MATHJAX_CONFIG = (
    "<script>\n"
    "MathJax = {\n"
    '  tex: {inlineMath: [["$", "$"], ["\\\\(", "\\\\)"]],\n'
    '        displayMath: [["$$", "$$"], ["\\\\[", "\\\\]"]]},\n'
    '  options: {ignoreHtmlClass: "mathjax-ignore",\n'
    '            processHtmlClass: "arithmatex"}\n'
    "};\n"
    "</script>\n"
    '<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" defer></script>'
)

MERMAID_JS = '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
MERMAID_INIT = (
    "<script>mermaid.initialize({startOnLoad:true,theme:'default'});</script>"
)


def _normalize_latex(latex: str) -> str:
    """Normalize LaTeX for MathJax compatibility.
    - Replace \\_ with _ inside \\text{} blocks (MathJax bug #1770)."""

    def _fix_text_block(m):
        return r"\text{" + m.group(1).replace(r"\_", "_") + "}"

    return re.sub(r"\\text\{((?:[^{}]|\{[^{}]*\})*)\}", _fix_text_block, latex)


def _escape_math(text: str) -> tuple[str, dict[str, str]]:
    """Replace $$...$$ and $...$ with placeholders to protect from markdown processing.
    Returns (processed_text, placeholder_map)."""
    placeholders = {}
    counter = [0]

    def _store_block(m):
        key = f"\x00MATHBLOCK{counter[0]}\x00"
        latex = _normalize_latex(m.group(1))
        html = f'<div class="arithmatex">\\[\n{latex}\n\\]</div>'
        placeholders[key] = html
        counter[0] += 1
        return key

    def _store_inline(m):
        key = f"\x00MATHINLINE{counter[0]}\x00"
        latex = _normalize_latex(m.group(1))
        html = f'<span class="arithmatex">\\({latex}\\)</span>'
        placeholders[key] = html
        counter[0] += 1
        return key

    text = re.sub(r"\$\$(.+?)\$\$", _store_block, text, flags=re.DOTALL)
    text = re.sub(r"\$([^$\n|]+?)\$", _store_inline, text)
    return text, placeholders


def _restore_math(text: str, placeholders: dict[str, str]) -> str:
    """Replace placeholders with MathJax-compatible HTML."""
    for key, html in placeholders.items():
        text = text.replace(key, html)
    return text


def _fix_unordered_lists(text: str) -> str:
    """Insert blank line before unordered list items that follow a non-list non-blank line.

    Python-Markdown follows CommonMark: unordered lists (``-``, ``*``) cannot interrupt
    paragraphs without a blank line, unlike ordered lists (``1.``) which can.  This
    preprocessor adds the missing blank line so that bullet lists render correctly.
    """
    lines = text.split("\n")
    result: list[str] = []
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue
        is_bullet = bool(re.match(r"^[-*] ", stripped))
        if is_bullet:
            prev_was_list = False
            for j in range(i - 1, -1, -1):
                prev = lines[j].strip()
                if prev:
                    prev_was_list = bool(re.match(r"^[-*] ", prev))
                    break
            if not prev_was_list and result and result[-1].strip():
                result.append("")
        result.append(line)
    return "\n".join(result)


def md_to_html(md_path: str, out_path: Optional[str] = None) -> str:
    """Convert a Markdown file to a standalone HTML file."""
    src = Path(md_path)
    if not src.exists():
        sys.exit(f"文件不存在: {md_path}")

    md_text = src.read_text(encoding="utf-8")
    md_text, math_placeholders = _escape_math(md_text)
    md_text = _fix_unordered_lists(md_text)

    md = markdown.Markdown(
        extensions=[
            "markdown.extensions.tables",
            "markdown.extensions.fenced_code",
            "markdown.extensions.toc",
            "markdown.extensions.nl2br",
            "markdown.extensions.sane_lists",
            "markdown.extensions.attr_list",
            "pymdownx.tasklist",
            "pymdownx.superfences",
        ],
        extension_configs={
            "pymdownx.superfences": {
                "custom_fences": [
                    {"name": "mermaid", "class": "mermaid", "format": _mermaid_format}
                ]
            },
            "pymdownx.tasklist": {"custom_checkbox": True, "clickable_checkbox": False},
        },
    )

    body = md.convert(md_text)
    body = _restore_math(body, math_placeholders)

    title = _extract_title(md_text) or src.stem

    html = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>{CSS}</style>
</head>
<body>
{body}
{MERMAID_JS}
{MERMAID_INIT}
{MATHJAX_CONFIG}
</body>
</html>"""

    if out_path:
        Path(out_path).write_text(html, encoding="utf-8")
        print(f"已生成: {out_path}")
    return html


def _mermaid_format(source: str, language: str, *args, **kwargs) -> str:
    """Wrap mermaid source code in a div for client-side rendering."""
    return f'<div class="mermaid">\n{source}\n</div>'


def _extract_title(text: str) -> Optional[str]:
    """Extract the first H1 from markdown text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python md2html.py <input.md> [output.html]")
        print("示例: python md2html.py timeline_template.md output.html")
        sys.exit(1)

    md_file = sys.argv[1]
    html_file = (
        sys.argv[2]
        if len(sys.argv) > 2
        else Path(md_file).with_suffix(".html").as_posix()
    )
    md_to_html(md_file, html_file)
