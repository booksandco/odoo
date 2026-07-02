# Part of the booksandco custom addons. See LICENSE.
"""Pure, unit-testable helpers that shrink outgoing mailing HTML.

Every function here takes and returns an HTML string (or ``markupsafe.Markup``)
and MUST NOT touch the ORM, so it can be tested in isolation.

Tiers (see PLAN.md):
* Safe tier: ``minify_email_html``, ``html_to_text_no_css``,
  ``relocate_tracking_pixel``.
* Aggressive tier (off by default): ``strip_dead_classes``,
  ``trim_redundant_inline_defaults``.

All transforms are string/regex based and protect ``<style>``, ``<script>``,
``<pre>``, ``<textarea>`` and HTML comments (incl. ``[if mso]`` conditionals),
so Outlook/VML markup, DOCTYPE and namespaces are preserved byte-for-byte
outside the specific tokens/declarations we remove.
"""

import re

import markupsafe
from lxml import etree

from odoo.tools.mail import html2plaintext

# Classes that must never be stripped even when no surviving <style> rule
# references them (layout / snippet hooks). Extend via the
# ``mass_mailing_slim.class_allowlist`` config parameter.
DEFAULT_CLASS_ALLOWLIST = (
    "o_layout",
    "o_mail_snippet_general",
)

# Inline declarations force-injected by ``convert_inline`` that are safe to drop.
#   * inherited-property "inherit"/"unset" (already the default behaviour);
#   * border-style/border-radius set to their initial value;
#   * box-sizing:border-box, which mass_mailing_mail.scss re-applies via
#     ``.o_layout * { box-sizing: border-box !important; }`` in clients that
#     honour <style> (the aggressive tier is opt-in and documented as such).
_DROP_ALWAYS = {
    "text-align:inherit",
    "font-size:unset",
    "line-height:inherit",
    "border-style:none",
    "border-radius:0px",
    "border-radius:0",
    "box-sizing:border-box",
}
# border-width:0px only renders when a border-style is set; safe to drop when
# the same declaration block has no non-none border-style.
_DROP_IF_NO_BORDER = {"border-width:0px", "border-width:0"}

# Regions whose inner content must never be minified/rewritten.
_PROTECT_RE = re.compile(
    r"<(pre|textarea|script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
# Same as above but also protects HTML comments (for class/style rewriting we
# must not touch markup inside ``[if mso]`` conditional comments).
_PROTECT_REGIONS_RE = re.compile(
    r"<(pre|textarea|script|style)\b[^>]*>.*?</\1\s*>|<!--.*?-->",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Collapse only whitespace runs that contain a newline (template indentation)
# between two tags. Single inter-word spaces are left intact.
_INTERTAG_WS_RE = re.compile(r">[ \t\r]*\n[ \t\r\n]*<")
_PLACEHOLDER = "\x00SLIM{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00SLIM(\d+)\x00")

# Match the open-tracking pixel injected by mass_mailing (``/mail/track/.../blank.gif``).
_TRACK_IMG_RE = re.compile(r"<img\b[^>]*?/mail/track/[^>]*?>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

# Class/style attribute rewriting (leading whitespace captured so the whole
# attribute can be dropped when it becomes empty).
_CLASS_ATTR_RE = re.compile(r"(\s+)class\s*=\s*(\"|')(.*?)\2", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_RE = re.compile(r"(\s+)style\s*=\s*(\"|')(.*?)\2", re.IGNORECASE | re.DOTALL)
# Class tokens referenced by CSS selectors, e.g. ``.o_layout``, ``.btn:hover``.
_CSS_CLASS_TOKEN_RE = re.compile(r"\.(-?[_a-zA-Z][-\w]*)")
# Class tokens referenced by attribute selectors, e.g. ``[class*="col-"]``.
_ATTR_CLASS_SEL_RE = re.compile(r"\[class[^\]]*?[\"']([^\"']+)[\"']", re.IGNORECASE)
# A border-style set to something other than ``none`` in a declaration block.
_BORDER_STYLE_SET_RE = re.compile(
    r"border(?:-(?:top|right|bottom|left))?-style\s*:\s*(?!none)[a-z]", re.IGNORECASE
)


def _as_same_type(original, text):
    """Return ``text`` wrapped in the same type as ``original`` (Markup or str)."""
    if isinstance(original, markupsafe.Markup):
        return markupsafe.Markup(text)
    return text


def _is_conditional_comment(comment):
    low = comment.lower()
    return "[if" in low or "endif" in low


def _protect_regions(text):
    """Replace protected regions with placeholders; return (text, regions)."""
    regions = []

    def _sub(match):
        regions.append(match.group(0))
        return _PLACEHOLDER.format(len(regions) - 1)

    return _PROTECT_REGIONS_RE.sub(_sub, text), regions


def _restore_regions(text, regions):
    return _PLACEHOLDER_RE.sub(lambda m: regions[int(m.group(1))], text)


def minify_email_html(html):
    """Collapse indentation between tags and drop non-Outlook comments.

    Conservative by construction: only whitespace runs *containing a newline*
    and located *between tags* are removed, so single inter-word spaces are
    preserved. ``[if ...]/[endif]`` conditional comments and the content of
    ``<pre>/<textarea>/<script>/<style>`` are left untouched.
    """
    if not html:
        return html
    text = str(html)

    # 1) Protect regions that must not be minified.
    protected = []

    def _protect(match):
        protected.append(match.group(0))
        return _PLACEHOLDER.format(len(protected) - 1)

    text = _PROTECT_RE.sub(_protect, text)

    # 2) Drop HTML comments except Outlook conditionals.
    def _strip_comment(match):
        return match.group(0) if _is_conditional_comment(match.group(0)) else ""

    text = _COMMENT_RE.sub(_strip_comment, text)

    # 3) Collapse indentation between tags.
    text = _INTERTAG_WS_RE.sub("><", text)

    # 4) Restore protected regions.
    text = _PLACEHOLDER_RE.sub(lambda m: protected[int(m.group(1))], text)

    return _as_same_type(html, text)


def html_to_text_no_css(html):
    """Plain-text conversion that first removes ``<style>``/``<script>`` text.

    Fixes the CSS-in-plain-text leak: ``odoo.tools.html2plaintext`` strips tags
    but not the *text content* of ``<style>``/``<script>`` elements, so raw CSS
    ends up in the ``text/plain`` MIME part. We drop those elements first.
    Never raises: on any parsing issue it falls back to plain ``html2plaintext``.
    """
    if not html:
        return html2plaintext(html or "")
    text = str(html)
    try:
        tree = etree.fromstring(text, parser=etree.HTMLParser())
        if tree is not None:
            etree.strip_elements(tree, "style", "script", with_tail=False)
            text = etree.tostring(tree, encoding="unicode", method="html")
    except Exception:  # noqa: BLE001 - sending must never crash on our helper
        pass
    return html2plaintext(text)


def relocate_tracking_pixel(html, pixel_html=None):
    """Move the open-tracking pixel to just after ``<body>``.

    mass_mailing appends the ``/mail/track/.../blank.gif`` pixel at the very end
    of the body, so a Gmail clip prevents it from ever loading (opens read 0).
    Moving it to the top restores open tracking on clipped mails. Idempotent.
    """
    if not html:
        return html
    text = str(html)
    matches = _TRACK_IMG_RE.findall(text)
    if not matches:
        return html
    pixel = pixel_html or matches[-1]
    text_wo = _TRACK_IMG_RE.sub("", text)
    body_match = _BODY_OPEN_RE.search(text_wo)
    if body_match:
        insert_at = body_match.end()
        text_new = text_wo[:insert_at] + pixel + text_wo[insert_at:]
    else:
        text_new = pixel + text_wo
    return _as_same_type(html, text_new)


def strip_dead_classes(html, allowlist=DEFAULT_CLASS_ALLOWLIST):
    """Remove class tokens not referenced by any surviving <style>/comment rule.

    After CSS inlining, most ``class`` attributes are dead weight: the styling
    already lives in ``style="..."``. Only classes matched by a surviving
    ``<style>`` block (``:hover``, ``@media``, design-element rules) or used by
    Outlook ``[if mso]`` markup still do anything. We keep exactly those (plus
    an allowlist) and drop the rest.

    Safe direction: the keep-set is an *over*-approximation (any ``.token`` seen
    in a ``<style>`` or comment is kept), so we never remove a class a surviving
    rule needs. Markup inside protected regions (incl. ``[if mso]`` comments) is
    never rewritten. Idempotent.
    """
    if not html:
        return html
    text = str(html)
    protected_text, regions = _protect_regions(text)

    keep = set(allowlist or ())
    for chunk in regions:
        # Only <style> blocks and comments can carry class references we must honour.
        if chunk[:6].lower() == "<style" or chunk[:4] == "<!--":
            keep.update(m.group(1) for m in _CSS_CLASS_TOKEN_RE.finditer(chunk))
            for m in _ATTR_CLASS_SEL_RE.finditer(chunk):
                keep.update(m.group(1).split())

    def _rewrite(match):
        leading, quote, value = match.group(1), match.group(2), match.group(3)
        kept = [tok for tok in value.split() if tok in keep]
        if not kept:
            return ""  # drop the whole (now empty) class attribute
        return f"{leading}class={quote}{' '.join(kept)}{quote}"

    protected_text = _CLASS_ATTR_RE.sub(_rewrite, protected_text)
    return _as_same_type(html, _restore_regions(protected_text, regions))


def apply_pipeline(html, flags, allowlist=DEFAULT_CLASS_ALLOWLIST):
    """Apply the whole server-side slim pipeline to ``html``.

    ``flags`` is a dict with the same keys used by the model override:
    ``move_pixel``, ``strip_classes``, ``trim_defaults``, ``minify``.
    Used both at send time and by ``mailing.email_size_kb`` so the editor
    warning reflects the size that will actually be sent.
    """
    if not html:
        return html
    body = html
    if flags.get("move_pixel"):
        body = relocate_tracking_pixel(body)
    if flags.get("strip_classes"):
        body = strip_dead_classes(body, allowlist=allowlist)
    if flags.get("trim_defaults"):
        body = trim_redundant_inline_defaults(body)
    if flags.get("minify"):
        body = minify_email_html(body)
    return body


def trim_redundant_inline_defaults(html):
    """Delete no-op inline declarations force-injected by the JS inliner.

    Removes declarations that resolve to their initial/inherited value on every
    matched element (see ``_DROP_ALWAYS`` / ``_DROP_IF_NO_BORDER``). Declarations
    inside protected regions are untouched. Idempotent.
    """
    if not html:
        return html
    text = str(html)
    protected_text, regions = _protect_regions(text)

    def _rewrite(match):
        leading, quote, value = match.group(1), match.group(2), match.group(3)
        has_border = bool(_BORDER_STYLE_SET_RE.search(value))
        kept = []
        for decl in value.split(";"):
            if not decl.strip():
                continue
            norm = re.sub(r"\s+", "", decl).lower()
            if norm.endswith("!important"):
                norm = norm[: -len("!important")]
            if norm in _DROP_ALWAYS:
                continue
            if norm in _DROP_IF_NO_BORDER and not has_border:
                continue
            kept.append(decl.strip())
        if not kept:
            return ""  # drop the whole (now empty) style attribute
        return f"{leading}style={quote}{';'.join(kept)}{quote}"

    protected_text = _STYLE_ATTR_RE.sub(_rewrite, protected_text)
    return _as_same_type(html, _restore_regions(protected_text, regions))
