"""Tiny Markdown→HTML renderer for the chat panel.

Supports: fenced code blocks (```), inline `code`, **bold**, *italic*,
headings (#..), bullet lists (- / *), and paragraph breaks. Everything is
HTML-escaped first so model output can't inject markup.
"""

from __future__ import annotations

import html
import re

from .styles import COLORS


_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def to_html(text: str) -> str:
    text = text or ""
    parts: list[str] = []
    last = 0
    for m in _FENCE.finditer(text):
        # text before this code block → inline-formatted
        parts.append(_render_inline_block(text[last:m.start()]))
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        parts.append(_render_code_block(code, lang))
        last = m.end()
    parts.append(_render_inline_block(text[last:]))
    return "".join(p for p in parts if p)


def _render_code_block(code: str, lang: str) -> str:
    escaped = html.escape(code.rstrip("\n"))
    label = (
        f'<div style="color:{COLORS["muted"]};font-size:8pt;'
        f'margin:6px 0 2px 2px;">{html.escape(lang)}</div>'
        if lang else ""
    )
    return (
        label
        + f'<pre style="background:{COLORS["code_bg"]};'
        f'border:1px solid {COLORS["code_border"]};border-radius:8px;'
        f'padding:10px 12px;margin:4px 0;white-space:pre-wrap;'
        f'font-family:Consolas,\'Cascadia Mono\',monospace;'
        f'color:#dfe6f0;">{escaped}</pre>'
    )


def _render_inline_block(segment: str) -> str:
    if not segment.strip():
        return ""
    out_lines: list[str] = []
    in_list = False
    for raw_line in segment.split("\n"):
        line = raw_line.rstrip()
        stripped = line.lstrip()

        # bullet list
        if stripped.startswith(("- ", "* ")):
            if not in_list:
                out_lines.append('<ul style="margin:4px 0 4px 18px;padding:0;">')
                in_list = True
            out_lines.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if in_list:
            out_lines.append("</ul>")
            in_list = False

        if not stripped:
            out_lines.append("<div style='height:6px;'></div>")
            continue

        # headings
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            content = _inline(stripped[level:].strip())
            size = max(10, 15 - level)
            out_lines.append(
                f'<div style="color:{COLORS["accent2"]};font-weight:700;'
                f'font-size:{size}pt;margin:6px 0 2px;">{content}</div>'
            )
            continue

        out_lines.append(f"<div>{_inline(line)}</div>")

    if in_list:
        out_lines.append("</ul>")
    return "".join(out_lines)


def _inline(text: str) -> str:
    # Protect inline code spans from other formatting, escape the rest.
    tokens: list[str] = []

    def _stash_code(m: re.Match) -> str:
        tokens.append(
            f'<code style="background:{COLORS["panel_hi"]};border:1px solid '
            f'{COLORS["border"]};border-radius:4px;padding:1px 5px;'
            f'font-family:Consolas,monospace;color:{COLORS["accent2"]};">'
            f'{html.escape(m.group(1))}</code>'
        )
        return f"\x00{len(tokens) - 1}\x00"

    tmp = _INLINE_CODE.sub(_stash_code, text)
    tmp = html.escape(tmp)
    tmp = _BOLD.sub(r"<b>\1</b>", tmp)
    tmp = _ITALIC.sub(r"<i>\1</i>", tmp)

    # restore code tokens (the \x00N\x00 markers survived escaping)
    def _restore(m: re.Match) -> str:
        return tokens[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, tmp)
