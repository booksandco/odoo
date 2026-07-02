# Part of the booksandco custom addons. See LICENSE.
"""Pure, unit-testable helpers that shrink outgoing mailing HTML.

Every function here takes and returns an HTML string (or ``markupsafe.Markup``)
and MUST NOT touch the ORM, so it can be tested in isolation.

Tiers (see PLAN.md):
* Safe tier: ``minify_email_html``, ``html_to_text_no_css``,
  ``relocate_tracking_pixel``.
* Aggressive tier (off by default): ``strip_dead_classes``,
  ``trim_redundant_inline_defaults``.
* Compression tier (off by default): ``normalize_css_values``,
  ``compress_shorthands``, ``minify_style_blocks``.

All transforms are string/regex based and protect ``<style>``, ``<script>``,
``<pre>``, ``<textarea>`` and HTML comments (incl. ``[if mso]`` conditionals),
so Outlook/VML markup, DOCTYPE and namespaces are preserved byte-for-byte
outside the specific tokens/declarations we remove.
"""

import re
from collections import Counter

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

# <style> block extraction (captures full element).
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.IGNORECASE | re.DOTALL)

# Extract simple property:value from CSS text (for inherited declarations).
_CSS_DECL_RE = re.compile(r"([\w\-]+)\s*:\s*([^;{}]+)")
# Quotes stripped when comparing CSS values: getComputedStyle re-quotes
# multi-word font families (``Arial, "Helvetica Neue", ...``) so the inline
# value would otherwise never match the unquoted shipped ``.o_layout`` value.
# The serialized ``style="..."`` attribute escapes those inner quotes as HTML
# entities, so we strip both the literal and entity forms.
_CSS_QUOTE_RE = re.compile(r"""['"]|&quot;|&\#34;|&apos;|&\#39;""", re.IGNORECASE)
# HTML entities (``&quot;``, ``&#34;`` ...) also end in ';', so a naive
# ``value.split(';')`` on an inline style value splits *inside* the entity and
# mangles the declaration. Protect entities before splitting.
_ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#[xX][0-9a-fA-F]+);")
_DECL_SENTINEL = "\x01{}\x01"
_DECL_SENTINEL_RE = re.compile(r"\x01(\d+)\x01")

# CSS value normalization.
_RGB_NAMED = {
    (0, 0, 0): "black",
    (255, 255, 255): "white",
    (255, 0, 0): "red",
    (0, 128, 0): "green",
    (0, 0, 255): "blue",
    (255, 255, 0): "yellow",
    (0, 255, 255): "cyan",
    (255, 0, 255): "magenta",
    (192, 192, 192): "silver",
    (128, 128, 128): "gray",
    (128, 0, 0): "maroon",
    (128, 128, 0): "olive",
    (0, 128, 128): "teal",
    (128, 0, 128): "purple",
    (0, 0, 128): "navy",
    (255, 165, 0): "orange",
}
_RGB_RE = re.compile(r"rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)")
_RGBA_RE = re.compile(r"rgba\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*0\s*\)")
_HEX_LONG_RE = re.compile(r"#([0-9a-fA-F])\1([0-9a-fA-F])\2([0-9a-fA-F])\3")
_FLOAT_PX_RE = re.compile(r"(\d+)\.0+(px|pt|em|rem|%)")
_ZERO_UNIT_RE = re.compile(r"\b0(?:\.0+)?(px|pt|em|rem|ex|ch|vw|vh|vmin|vmax|%|cm|mm|in|pc)\b")
_DECL_WS_RE = re.compile(r"\s*:\s*")

# Shorthand compression: maps property -> number of sides/corners to consider.
_SIDED_PROPS = ("padding", "margin", "border-width", "border-style", "border-color")
_RADIUS_PROP = "border-radius"
_SIDE_NAMES = ("top", "right", "bottom", "left")
# The four border-radius corner longhands, mapped to their [tl, tr, br, bl] index.
_RADIUS_LONGHANDS = {
    "border-top-left-radius": 0,
    "border-top-right-radius": 1,
    "border-bottom-right-radius": 2,
    "border-bottom-left-radius": 3,
}


def _longhand_name(base, side_idx):
    """Reconstruct the longhand property for a sided base + side index.

    ``padding``/``margin`` -> ``padding-top``; ``border-width`` etc. ->
    ``border-top-width``.
    """
    side = _SIDE_NAMES[side_idx]
    if base in ("padding", "margin"):
        return f"{base}-{side}"
    kind = base.split("-", 1)[1]  # width / style / color
    return f"border-{side}-{kind}"


def _as_same_type(original, text):
    """Return ``text`` wrapped in the same type as ``original`` (Markup or str)."""
    if isinstance(original, markupsafe.Markup):
        return markupsafe.Markup(text)
    return text


def _is_conditional_comment(comment):
    low = comment.lower()
    return "[if" in low or "endif" in low


def _norm_css_match(value):
    """Normalize a CSS value for equality comparison.

    Lowercases, drops all whitespace and quotes so that an inline value like
    ``Arial, "Helvetica Neue", Helvetica, sans-serif`` compares equal to the
    unquoted ``Arial,Helvetica Neue,Helvetica,sans-serif`` from the shipped CSS.
    """
    return _CSS_QUOTE_RE.sub("", re.sub(r"\s+", "", value.strip())).lower()


def _split_declarations(value):
    """Split an inline style value on ';' without breaking HTML entities.

    ``&quot;``/``&#34;`` and friends end in ';', so a plain ``split(';')`` would
    cut a ``font-family:...&quot;Helvetica Neue&quot;...`` declaration into
    fragments. We stash entities behind sentinels, split, then restore.
    """
    holders = []

    def _hold(m):
        holders.append(m.group(0))
        return _DECL_SENTINEL.format(len(holders) - 1)

    protected = _ENTITY_RE.sub(_hold, value)
    parts = protected.split(";")
    if not holders:
        return parts
    return [
        _DECL_SENTINEL_RE.sub(lambda m: holders[int(m.group(1))], p) for p in parts
    ]


def _protect_regions(text):
    """Replace protected regions with placeholders; return (text, regions)."""
    regions = []

    def _sub(match):
        regions.append(match.group(0))
        return _PLACEHOLDER.format(len(regions) - 1)

    return _PROTECT_REGIONS_RE.sub(_sub, text), regions


def _restore_regions(text, regions):
    return _PLACEHOLDER_RE.sub(lambda m: regions[int(m.group(1))], text)


def _normalize_value(value):
    """Normalize a single CSS value."""
    value = value.strip()
    if not value:
        return value

    # rgba(..., 0) -> transparent
    value = _RGBA_RE.sub("transparent", value)

    # rgb(r, g, b) -> named color when possible
    def _rgb_to_named(match):
        rgb = tuple(int(match.group(i)) for i in range(1, 4))
        return _RGB_NAMED.get(rgb, match.group(0))

    value = _RGB_RE.sub(_rgb_to_named, value)

    # #aabbcc -> #abc
    value = _HEX_LONG_RE.sub(r"#\1\2\3", value)

    # 16.0px -> 16px
    value = _FLOAT_PX_RE.sub(r"\1\2", value)

    # 0px / 0.0em / 0% -> 0
    value = _ZERO_UNIT_RE.sub("0", value)

    return value


def _normalize_declaration(decl):
    """Normalize property:value whitespace and value."""
    if ":" not in decl:
        return decl.strip()
    prop, val = decl.split(":", 1)
    return f"{prop.strip()}:{_normalize_value(val)}"


def _compress_sided(prop, parts):
    """Collapse 1-4 identical sides to the shortest shorthand form."""
    parts = [p.strip() for p in parts]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    if len(parts) == 4:
        if parts[0] == parts[1] == parts[2] == parts[3]:
            return parts[0]
        if parts[0] == parts[2] and parts[1] == parts[3]:
            return f"{parts[0]} {parts[1]}"
        if parts[1] == parts[3]:
            return f"{parts[0]} {parts[1]} {parts[2]}"
    return " ".join(parts)


def _compress_radius(parts):
    """Collapse 1-4 identical radii."""
    parts = [p.strip() for p in parts]
    if len(parts) == 4 and parts[0] == parts[1] == parts[2] == parts[3]:
        return parts[0]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return " ".join(parts)


def _rewrite_style_value(value):
    """Normalize and shorthand-compress one inline style declaration block."""
    # Normalize declarations.
    decls = [_normalize_declaration(d) for d in _split_declarations(value) if d.strip()]

    # Group side-specific sub-properties for shorthand compression.
    groups = {p: [None, None, None, None] for p in _SIDED_PROPS}
    remaining = []
    # Non-directional border longhands (border-width/style/color) that can become "border".
    border_longhands = {"width": None, "style": None, "color": None}
    for decl in decls:
        if ":" not in decl:
            remaining.append(decl)
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()

        # Non-directional border-* longhands.
        if prop in ("border-width", "border-style", "border-color"):
            border_longhands[prop[7:]] = val
            continue

        side = None
        if prop.startswith("padding-"):
            side = {"padding-top": 0, "padding-right": 1, "padding-bottom": 2, "padding-left": 3}.get(prop)
            base = "padding"
        elif prop.startswith("margin-"):
            side = {"margin-top": 0, "margin-right": 1, "margin-bottom": 2, "margin-left": 3}.get(prop)
            base = "margin"
        elif prop.startswith("border-") and prop.endswith(("-width", "-style", "-color")):
            side_map = {
                "border-top": 0, "border-right": 1, "border-bottom": 2, "border-left": 3,
            }
            for prefix, idx in side_map.items():
                if prop.startswith(prefix):
                    side = idx
                    break
            if prop.endswith("-width"):
                base = "border-width"
            elif prop.endswith("-style"):
                base = "border-style"
            else:
                base = "border-color"
        else:
            remaining.append(decl)
            continue

        if side is None:
            remaining.append(decl)
            continue
        groups[base][side] = val

    # Build shorthand declarations for sided properties.
    shorthand = []
    for prop, sides in groups.items():
        if all(s is None for s in sides):
            continue
        if any(s is None for s in sides):
            # A CSS shorthand always starts at the top value and implies all
            # four sides, so a partial set (e.g. only padding-left) cannot be
            # collapsed without changing semantics. Re-emit the present sides
            # as longhands, unchanged.
            for idx, val in enumerate(sides):
                if val is not None:
                    shorthand.append(f"{_longhand_name(prop, idx)}:{val}")
            continue
        shorthand.append(f"{prop}:{_compress_sided(prop, sides)}")

    # Merge non-directional border-* longhands into a single "border" shorthand
    # only when all three are present; otherwise keep whichever were set so we
    # never silently drop a declaration (e.g. a lone ``border-color``).
    if all(border_longhands.values()):
        shorthand.append(
            f"border:{border_longhands['width']} {border_longhands['style']} {border_longhands['color']}"
        )
    else:
        for name, val in border_longhands.items():
            if val is not None:
                shorthand.append(f"border-{name}:{val}")

    # border-radius shorthand compression. Only collapse when all four corners
    # are present (a shorthand implies all four); otherwise leave the corner
    # longhands untouched. Note the ``:`` guard: ``remaining`` may hold entity
    # sentinels or malformed fragments with no colon.
    radius_parts = [None, None, None, None]
    for decl in remaining:
        if ":" not in decl:
            continue
        pl = decl.split(":", 1)[0].strip().lower()
        if pl in _RADIUS_LONGHANDS:
            radius_parts[_RADIUS_LONGHANDS[pl]] = decl.split(":", 1)[1].strip()
    if all(r is not None for r in radius_parts):
        # Drop only the four radius longhands (not every ``border-*`` decl such
        # as border-collapse) and replace them with the compressed shorthand.
        remaining = [
            d for d in remaining
            if ":" not in d or d.split(":", 1)[0].strip().lower() not in _RADIUS_LONGHANDS
        ]
        remaining.append(f"border-radius:{_compress_radius(radius_parts)}")

    result = ";".join(remaining + shorthand)
    # Collapse whitespace after colon once more.
    result = _DECL_WS_RE.sub(":", result)
    return result


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


def strip_dead_classes(html, allowlist=DEFAULT_CLASS_ALLOWLIST, shipped_style_css=None):
    """Remove class tokens not referenced by any surviving <style>/comment rule.

    After CSS inlining, most ``class`` attributes are dead weight: the styling
    already lives in ``style="..."``. Only classes matched by a surviving
    ``<style>`` block (``:hover``, ``@media``, design-element rules) or used by
    Outlook ``[if mso]`` markup still do anything. We keep exactly those (plus
    an allowlist) and drop the rest.

    ``shipped_style_css`` is optional. When provided, the keep-set is built from
    the CSS text that actually ships with the email (e.g. the head
    ``mass_mailing_mail.scss``). This is more aggressive than scanning every
    ``<style>`` element in the body, because the inliner injects many rules that
    only matter inside the editor iframe.

    Markup inside protected regions (incl. ``[if mso]`` comments) is never
    rewritten. Idempotent.
    """
    if not html:
        return html
    text = str(html)
    protected_text, regions = _protect_regions(text)

    keep = set(allowlist or ())
    if shipped_style_css:
        keep.update(m.group(1) for m in _CSS_CLASS_TOKEN_RE.finditer(shipped_style_css))
        for m in _ATTR_CLASS_SEL_RE.finditer(shipped_style_css):
            keep.update(m.group(1).split())
        # Also scan [if mso] <style> blocks inside comments.
        for chunk in regions:
            if chunk.startswith("<!--") and "[if" in chunk.lower():
                keep.update(m.group(1) for m in _CSS_CLASS_TOKEN_RE.finditer(chunk))
                for m in _ATTR_CLASS_SEL_RE.finditer(chunk):
                    keep.update(m.group(1).split())
    else:
        for chunk in regions:
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


def normalize_css_values(html):
    """Normalize CSS values inside inline ``style=`` and ``<style>`` blocks.

    Rewrites rgb() to named colors, shortens hex colors, strips trailing zeros
    from pixel values, and converts ``0px``/``0em``/``0%`` to ``0``.
    """
    if not html:
        return html
    text = str(html)

    # Process <style> blocks first, before protecting regions, so we actually
    # touch their CSS content.
    def _rewrite_style_block(match):
        open_tag, css, close_tag = match.group(1), match.group(2), match.group(3)
        css = css.replace("\n", " ")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        css = re.sub(r"\s*([{}:;,])\s*", r"\1", css)
        css = _RGBA_RE.sub("transparent", css)

        def _rgb_sub(m):
            rgb = tuple(int(m.group(i)) for i in range(1, 4))
            return _RGB_NAMED.get(rgb, m.group(0))

        css = _RGB_RE.sub(_rgb_sub, css)
        css = _HEX_LONG_RE.sub(r"#\1\2\3", css)
        css = _FLOAT_PX_RE.sub(r"\1\2", css)
        css = _ZERO_UNIT_RE.sub("0", css)
        css = re.sub(r";+", ";", css)
        return f"{open_tag}{css.strip()}{close_tag}"

    text = _STYLE_BLOCK_RE.sub(_rewrite_style_block, text)

    # Now protect pre/textarea/script/conditional comments and process inline styles.
    protected_text, regions = _protect_regions(text)

    def _rewrite_style(match):
        leading, quote, value = match.group(1), match.group(2), match.group(3)
        new_value = _rewrite_style_value(value)
        if not new_value:
            return ""
        return f"{leading}style={quote}{new_value}{quote}"

    protected_text = _STYLE_ATTR_RE.sub(_rewrite_style, protected_text)
    return _as_same_type(html, _restore_regions(protected_text, regions))


def compress_shorthands(html):
    """Compress verbose longhand padding/margin/border-* declarations.

    Converts ``padding: 10px 10px 10px 10px`` to ``padding: 10px`` and similar
    for margin/border-width/border-style/border-color. Also collapses
    ``border-*`` longhands into a single ``border`` shorthand when all three
    width/style/color are present, and compresses
    ``border-radius`` longhands. Inline ``style=`` attributes only.
    """
    if not html:
        return html
    text = str(html)
    protected_text, regions = _protect_regions(text)

    def _rewrite(match):
        leading, quote, value = match.group(1), match.group(2), match.group(3)
        new_value = _rewrite_style_value(value)
        if not new_value:
            return ""
        return f"{leading}style={quote}{new_value}{quote}"

    protected_text = _STYLE_ATTR_RE.sub(_rewrite, protected_text)
    return _as_same_type(html, _restore_regions(protected_text, regions))


def minify_style_blocks(html):
    """Remove comments/whitespace from ``<style>`` block contents.

    Leaves conditional comments untouched. This is a lightweight minification;
    SCSS comments (``//``) are already compiled away by the time the CSS is
    injected into the email.
    """
    if not html:
        return html
    text = str(html)

    def _rewrite(match):
        open_tag, css, close_tag = match.group(1), match.group(2), match.group(3)
        css = css.replace("\n", " ")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        css = re.sub(r"\s*([{}:;,])\s*", r"\1", css)
        css = re.sub(r";+", ";", css)
        return f"{open_tag}{css.strip()}{close_tag}"

    text = _STYLE_BLOCK_RE.sub(_rewrite, text)
    # Restore/protect nothing else; inline styles are left untouched by design.
    return _as_same_type(html, text)


def apply_pipeline(html, flags, allowlist=DEFAULT_CLASS_ALLOWLIST, shipped_style_css=None):
    """Apply the whole server-side slim pipeline to ``html``.

    ``flags`` is a dict with the same keys used by the model override:
    ``move_pixel``, ``strip_classes``, ``trim_defaults``, ``minify``,
    ``normalize_css``, ``compress_shorthands``, ``minify_style_blocks``,
    ``strip_inherited``.
    Used both at send time and by ``mailing.email_size_kb`` so the editor
    warning reflects the size that will actually be sent.
    """
    if not html:
        return html
    body = html
    if flags.get("move_pixel"):
        body = relocate_tracking_pixel(body)
    if flags.get("strip_classes"):
        body = strip_dead_classes(body, allowlist=allowlist, shipped_style_css=shipped_style_css)
    if flags.get("strip_inherited"):
        body = strip_inherited_declarations(body, shipped_style_css=shipped_style_css)
    if flags.get("trim_defaults"):
        body = trim_redundant_inline_defaults(body)
    if flags.get("normalize_css"):
        body = normalize_css_values(body)
    if flags.get("compress_shorthands"):
        body = compress_shorthands(body)
    if flags.get("minify_style_blocks"):
        body = minify_style_blocks(body)
    if flags.get("minify"):
        body = minify_email_html(body)
    return body


def strip_inherited_declarations(html, shipped_style_css=None, inherited_map=None):
    """Remove inline declarations that match inherited/shipped default values.

    ``font-family``, ``color``, ``line-height`` and ``font-size`` are inherited
    by default in CSS. The JS inliner stamps the same value onto hundreds of
    elements even though a parent (``body``, ``.o_layout``) already sets it.
    This transform strips those redundant declarations.

    ``inherited_map`` is ``{property: {value1, value2, ...}}``. When omitted, it
    is auto-built from ``shipped_style_css`` by looking at rules for
    ``.o_layout``, ``body`` and ``*`` selectors.
    """
    if not html:
        return html
    if inherited_map is None:
        inherited_map = _build_inherited_map(shipped_style_css or "")
    if not inherited_map:
        return html

    text = str(html)
    protected_text, regions = _protect_regions(text)

    def _rewrite(match):
        leading, quote, value = match.group(1), match.group(2), match.group(3)
        kept = []
        for decl in _split_declarations(value):
            if not decl.strip():
                continue
            if ":" not in decl:
                kept.append(decl.strip())
                continue
            prop, val = decl.split(":", 1)
            prop = prop.strip().lower()
            val_norm = _norm_css_match(val)
            drop_values = {_norm_css_match(v) for v in inherited_map.get(prop, ())}
            if val_norm in drop_values:
                continue
            kept.append(decl.strip())
        if not kept:
            return ""
        return f"{leading}style={quote}{';'.join(kept)}{quote}"

    protected_text = _STYLE_ATTR_RE.sub(_rewrite, protected_text)
    return _as_same_type(html, _restore_regions(protected_text, regions))


def _build_inherited_map(css_text):
    """Build {property: {values}} for inherited declarations set at root level.

    We look at selectors that establish defaults for the whole email:
    ``.o_layout``, ``body``, ``body:has(.o_layout)``, ``*`` and ``html``.
    Only inherited properties are collected (font-family, color, line-height,
    font-size, text-align).
    """
    inherited_props = {"font-family", "color", "line-height", "font-size", "text-align"}
    result = {p: set() for p in inherited_props}
    # Strip comments, then split into rough rule blocks.
    css = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
    # Match rule blocks.
    for block in re.finditer(r"([^{}]+)\{([^}]*)\}", css, flags=re.DOTALL):
        selector = block.group(1).strip()
        # Only consider selectors that apply broadly to the email wrapper.
        if not any(selector.startswith(s) or (" " + s) in selector for s in (".o_layout", "body", "html", "*")):
            continue
        for m in _CSS_DECL_RE.finditer(block.group(2)):
            prop = m.group(1).strip().lower()
            if prop in inherited_props:
                result[prop].add(m.group(2).strip())
    # Remove empty sets.
    return {k: v for k, v in result.items() if v}


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
        for decl in _split_declarations(value):
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


def diagnose_bloat(html, top_n=10):
    """Return a dict showing where bytes are spent in ``html``.

    Non-mutating. Useful for understanding why a particular mailing is still
    large after slimming.
    """
    if not html:
        return {
            "total_bytes": 0,
            "inline_style_bytes": 0,
            "class_attr_bytes": 0,
            "style_block_bytes": 0,
            "top_inline_styles": [],
            "top_classes": [],
        }
    text = str(html)
    total = len(text.encode("utf-8"))

    inline_style_bytes = 0
    style_counter = Counter()
    for m in _STYLE_ATTR_RE.finditer(text):
        val = m.group(3)
        inline_style_bytes += len(val)
        style_counter[val] += 1

    class_attr_bytes = 0
    class_counter = Counter()
    for m in _CLASS_ATTR_RE.finditer(text):
        val = m.group(3)
        class_attr_bytes += len(val)
        for tok in val.split():
            class_counter[tok] += 1

    style_block_bytes = 0
    for m in _STYLE_BLOCK_RE.finditer(text):
        style_block_bytes += len(m.group(2))

    return {
        "total_bytes": total,
        "inline_style_bytes": inline_style_bytes,
        "class_attr_bytes": class_attr_bytes,
        "style_block_bytes": style_block_bytes,
        "top_inline_styles": style_counter.most_common(top_n),
        "top_classes": class_counter.most_common(top_n),
    }
