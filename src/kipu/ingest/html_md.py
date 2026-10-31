"""Minimal HTML → markdown conversion that keeps comments and <meta> tags as raw HTML lines."""

import html

from lxml import etree
from lxml import html as lxml_html

BLOCK_HEADINGS = {f"h{i}": "#" * i for i in range(1, 7)}
SKIP_TAGS = {"style", "script", "title", "head"}


def html_to_markdown(source: str) -> tuple[str, dict[str, str]]:
    """Return (markdown body, metadata from <meta name=... content=...>)."""
    root = lxml_html.document_fromstring(source)
    metadata: dict[str, str] = {}
    meta_lines: list[str] = []
    for meta in root.iter("meta"):
        name = meta.get("name")
        if name:
            content = meta.get("content", "")
            metadata[name] = content
            meta_lines.append(f'<meta name="{html.escape(name)}" content="{html.escape(content)}">')
    body = root.find("body")
    blocks = _blocks(body if body is not None else root)
    head_comments = [_comment(c) for c in _head_comments(root)]
    parts = ["\n".join(meta_lines + head_comments), *blocks]
    return "\n\n".join(p for p in parts if p.strip()) + "\n", metadata


def _head_comments(root: etree._Element) -> list[etree._Element]:
    head = root.find("head")
    return [c for c in head.iter(etree.Comment)] if head is not None else []


def _comment(node: etree._Element) -> str:
    return f"<!--{node.text or ''}-->"


def _blocks(node: etree._Element) -> list[str]:
    out: list[str] = []
    for child in node:
        if child.tag is etree.Comment:
            out.append(_comment(child))
        elif not isinstance(child.tag, str) or child.tag in SKIP_TAGS:
            continue
        elif child.tag in BLOCK_HEADINGS:
            out.append(f"{BLOCK_HEADINGS[child.tag]} {_inline(child).strip()}")
        elif child.tag == "table":
            out.append(_table(child))
        elif child.tag in ("ul", "ol"):
            out.append(_list(child))
        elif child.tag in ("div", "section", "article", "main", "header", "footer"):
            out.extend(_blocks(child))
        elif child.tag == "pre":
            out.append(f"```\n{child.text_content()}\n```")
        else:
            out.append(_inline(child).strip())
        if child.tail and child.tail.strip():
            out.append(child.tail.strip())
    return out


def _inline(node: etree._Element) -> str:
    parts = [node.text or ""]
    for child in node:
        if child.tag is etree.Comment:
            parts.append(_comment(child))
        elif isinstance(child.tag, str):
            parts.append(_inline_tag(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _inline_tag(node: etree._Element) -> str:
    inner = _inline(node)
    if node.tag in ("strong", "b"):
        return f"**{inner}**"
    if node.tag in ("em", "i"):
        return f"*{inner}*"
    if node.tag == "br":
        return "\n"
    if node.tag == "code":
        return f"`{inner}`"
    if node.tag == "a":
        href = node.get("href", "")
        if not href or href.removeprefix("mailto:") == inner:
            return inner
        return f"[{inner}]({href})"
    return inner


def _list(node: etree._Element) -> str:
    marker = "1." if node.tag == "ol" else "-"
    return "\n".join(f"{marker} {_inline(li).strip()}" for li in node if li.tag == "li")


def _table(node: etree._Element) -> str:
    rows = [
        [_inline(cell).strip().replace("|", "\\|") for cell in tr if cell.tag in ("td", "th")]
        for tr in node.iter("tr")
    ]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)
