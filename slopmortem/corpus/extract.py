"""HTML sanitize and extract pipeline for raw source documents.

Pipeline (spec line 244): sanitize HTML -> trafilatura -> readability
fallback -> length floor (<500 chars => empty). The sanitizer runs BEFORE
trafilatura: trafilatura otherwise treats HTML comments, JSON-LD, hidden
nodes, and attribute text as visible, opening an indirect-injection surface.

The stripped surfaces are pinned by the hostile-fixture test in
``tests/sources/test_extract_visible_text_only.py`` and must match spec
line 244: comments, ``<script>``/``<style>``/``<noscript>``, JSON-LD
scripts, ``display:none``/``visibility:hidden``/``hidden`` nodes, and
``aria-label``/``alt``/``title`` attributes.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportAny=false
# lxml ships no type stubs, and pulling `lxml-stubs` into the dep list isn't
# worth it for this module alone. Module-wide suppression is local to the
# sanitizer; the hostile-fixture test pins the actual surfaces.

from __future__ import annotations

import re
from typing import Any, cast

import lxml.etree
import lxml.html
import trafilatura
from lxml.etree import Comment
from readability import Document

LENGTH_FLOOR = 500

_HIDDEN_STYLE_RE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_STRIPPED_TAGS = ("script", "style", "noscript")
_STRIPPED_ATTRS = ("aria-label", "alt", "title")


def _drop_node(node: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Detach *node* from its parent, or clear it in place if it's the root."""
    parent = node.getparent()
    if parent is None:
        node.clear()
        return
    parent.remove(node)


def _drop_comments(root: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Remove HTML comments, including ``<!-- IMPORTANT: ... -->`` injections."""
    for comment in root.xpath("//comment()"):
        _drop_node(comment)
    for comment in list(root.iter(Comment)):
        _drop_node(comment)


def _drop_stripped_tags(root: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Remove ``<script>``, ``<style>``, and ``<noscript>`` (including JSON-LD)."""
    for tag in _STRIPPED_TAGS:
        for node in root.iter(tag):
            _drop_node(node)


def _drop_hidden_nodes(root: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Remove nodes hidden by the ``hidden`` attribute or by display/visibility CSS."""
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        if node.get("hidden") is not None:
            _drop_node(node)
            continue
        style = node.get("style") or ""
        if _HIDDEN_STYLE_RE.search(style):
            _drop_node(node)


def _strip_attribute_text(root: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Strip ``aria-label``, ``alt``, and ``title`` so trafilatura can't lift their text."""
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for attr in _STRIPPED_ATTRS:
            if attr in node.attrib:
                del node.attrib[attr]


def sanitize_html(html: str) -> str:
    """Strip injection-surface nodes and attributes from *html*.

    Args:
        html: Raw HTML bytes/text from a fetched page.

    Returns:
        Cleaned HTML string ready to hand to a content extractor.
    """
    if not html or not html.strip():
        return ""
    try:
        root = lxml.html.fromstring(html)
    except lxml.etree.ParserError, ValueError:
        return ""

    _drop_comments(root)
    _drop_stripped_tags(root)
    _drop_hidden_nodes(root)
    _strip_attribute_text(root)

    out = lxml.html.tostring(root, encoding="unicode")
    return cast("str", out)


def _trafilatura_extract(html: str) -> str:
    out = trafilatura.extract(html, include_comments=False, include_tables=False)
    return out or ""


def _readability_extract(html: str) -> str:
    try:
        doc = Document(html)
        summary_html = doc.summary(html_partial=True)
    except Exception:  # noqa: BLE001 — readability raises a grab-bag of types from lxml
        return ""
    try:
        root = lxml.html.fromstring(summary_html)
    except lxml.etree.ParserError, ValueError:
        return ""
    return cast("str", root.text_content()).strip()


def extract_clean(html: str) -> str:
    """Sanitize then extract main content; return ``""`` when below length floor.

    Args:
        html: Raw HTML from a fetched source page.

    Returns:
        Plain-text content if at least :data:`LENGTH_FLOOR` chars; empty string otherwise.
    """
    cleaned = sanitize_html(html)
    if not cleaned:
        return ""
    text = _trafilatura_extract(cleaned)
    if len(text) < LENGTH_FLOOR:
        text = _readability_extract(cleaned)
    if len(text) < LENGTH_FLOOR:
        return ""
    return text
