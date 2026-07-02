# Part of the booksandco custom addons. See LICENSE.
"""Unit tests for the mass_mailing_slim transforms.

Tests run on the odoo.sh build (no local runner) per AGENTS.md.
"""

from odoo.tests import common, tagged

from odoo.addons.mass_mailing_slim.tools import html_slim


@tagged("post_install", "-at_install")
class TestHtmlSlim(common.TransactionCase):

    # -- minify ---------------------------------------------------------------

    def test_minify_collapses_indentation(self):
        html = "<table>\n    <tr>\n        <td>Hi</td>\n    </tr>\n</table>"
        out = html_slim.minify_email_html(html)
        self.assertNotIn("\n    <", out)
        self.assertIn("<tr><td>Hi</td></tr>", out)

    def test_minify_preserves_single_spaces(self):
        html = "<p>Hello <b>bold</b> world</p>"
        out = html_slim.minify_email_html(html)
        self.assertEqual(out, html)  # nothing to collapse

    def test_minify_preserves_mso_comments(self):
        html = "<div>\n<!--[if mso]><table><tr><td>x</td></tr></table><![endif]-->\n</div>"
        out = html_slim.minify_email_html(html)
        self.assertIn("[if mso]", out)
        self.assertIn("[endif]", out)

    def test_minify_strips_plain_comments(self):
        html = "<div><!-- internal note -->Hello</div>"
        out = html_slim.minify_email_html(html)
        self.assertNotIn("internal note", out)
        self.assertIn("Hello", out)

    def test_minify_leaves_style_content_intact(self):
        html = "<style>\n.a {\n  color: red;\n}\n</style>\n<div>hi</div>"
        out = html_slim.minify_email_html(html)
        self.assertIn("color: red;", out)  # CSS interior untouched

    # -- plain-text fix -------------------------------------------------------

    def test_plaintext_drops_css(self):
        html = (
            "<html><head><style>.x{color:red;font-size:12px}</style></head>"
            "<body><p>Hello world</p></body></html>"
        )
        text = html_slim.html_to_text_no_css(html)
        self.assertIn("Hello world", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("{", text)

    def test_plaintext_empty(self):
        self.assertEqual(html_slim.html_to_text_no_css(""), "")

    # -- tracking pixel relocation -------------------------------------------

    def test_tracking_pixel_moved_to_top(self):
        html = (
            '<html><body><p>Body</p>'
            '<img src="https://ex.com/mail/track/5/tok/blank.gif"/></body></html>'
        )
        out = html_slim.relocate_tracking_pixel(html)
        self.assertLess(out.index("/mail/track/"), out.index("<p>"))
        self.assertEqual(out.count("/mail/track/"), 1)

    def test_relocate_idempotent(self):
        html = (
            '<html><body><p>Body</p>'
            '<img src="https://ex.com/mail/track/5/tok/blank.gif"/></body></html>'
        )
        once = html_slim.relocate_tracking_pixel(html)
        twice = html_slim.relocate_tracking_pixel(once)
        self.assertEqual(once, twice)

    def test_relocate_no_pixel_is_noop(self):
        html = "<html><body><p>Body</p></body></html>"
        self.assertEqual(html_slim.relocate_tracking_pixel(html), html)

    # -- aggressive: dead-class stripping -------------------------------------

    def test_strip_dead_classes_drops_unreferenced(self):
        html = "<div class='btn card-body mx-auto'>hi</div>"
        out = html_slim.strip_dead_classes(html)
        self.assertNotIn("class=", out)

    def test_strip_dead_classes_keeps_referenced(self):
        html = (
            "<style>.o_hover:hover { color: blue; }\n"
            "@media (max-width: 600px) { .s_stack { width: 100%; } }\u003c/style>"
            "<div class='btn o_hover s_stack'>hi</div>"
        )
        out = html_slim.strip_dead_classes(html)
        self.assertNotIn("btn", out)
        self.assertIn('o_hover', out)
        self.assertIn('s_stack', out)

    def test_strip_dead_classes_keeps_allowlist(self):
        html = "<div class='btn o_layout'>hi</div>"
        out = html_slim.strip_dead_classes(html)
        self.assertIn('o_layout', out)
        self.assertNotIn('btn', out)

    def test_strip_dead_classes_keeps_mso_referenced(self):
        html = (
            "<!--[if mso]><style>.mso_only { width: 600px; }\u003c/style><![endif]-->"
            "<div class='btn mso_only'>hi</div>"
        )
        out = html_slim.strip_dead_classes(html)
        self.assertIn('mso_only', out)
        self.assertNotIn('btn', out)

    def test_strip_dead_classes_shipped_css_only(self):
        """Passing shipped_style_css restricts the keep-set to that CSS."""
        html = (
            "<style>.editor_only { color: red; }\u003c/style>"
            "<div class='editor_only shipped_ref'>hi</div>"
        )
        shipped = ".shipped_ref { width: 100%; }"
        out = html_slim.strip_dead_classes(html, shipped_style_css=shipped)
        self.assertIn('shipped_ref', out)
        # The body <style> block is left untouched by strip_dead_classes (so the
        # literal ".editor_only" rule survives there), but the dead class was
        # removed from the element's @class attribute.
        self.assertIn("<div class='shipped_ref'>", out)
        self.assertNotIn("editor_only shipped_ref", out)

    # -- aggressive: trim redundant inline defaults ----------------------------

    def test_trim_defaults_drops_no_ops(self):
        html = "<div style='box-sizing:border-box;border-radius:0px;border-style:none'>hi</div>"
        out = html_slim.trim_redundant_inline_defaults(html)
        self.assertNotIn("box-sizing", out)
        self.assertNotIn("border-radius", out)
        self.assertNotIn("border-style", out)

    def test_trim_defaults_keeps_real_borders(self):
        html = "<div style='border-width:2px;border-style:solid;color:red'>hi</div>"
        out = html_slim.trim_redundant_inline_defaults(html)
        self.assertIn("border-width:2px", out)
        self.assertIn("border-style:solid", out)
        self.assertIn("color:red", out)

    def test_trim_defaults_drops_zero_width_when_no_border_style(self):
        html = "<div style='border-width:0px;color:red'>hi</div>"
        out = html_slim.trim_redundant_inline_defaults(html)
        self.assertNotIn("border-width", out)
        self.assertIn("color:red", out)

    def test_trim_defaults_keeps_zero_width_when_border_style_set(self):
        html = "<div style='border-width:0px;border-style:solid;color:red'>hi</div>"
        out = html_slim.trim_redundant_inline_defaults(html)
        self.assertIn("border-width:0px", out)
        self.assertIn("border-style:solid", out)

    # -- compression: CSS normalization ---------------------------------------

    def test_normalize_rgb_to_named(self):
        html = "<div style='color:rgb(255, 0, 0); background:rgb(0,0,0)'>x</div>"
        out = html_slim.normalize_css_values(html)
        self.assertIn("color:red", out)
        self.assertIn("background:black", out)

    def test_normalize_rgba_to_transparent(self):
        html = "<div style='color:rgba(0,0,0,0)'>x</div>"
        out = html_slim.normalize_css_values(html)
        self.assertIn("color:transparent", out)

    def test_normalize_hex_short(self):
        html = "<div style='color:#ff0000; border-color:#aabbcc'>x</div>"
        out = html_slim.normalize_css_values(html)
        self.assertIn("color:#f00", out)
        self.assertIn("border-color:#abc", out)

    def test_normalize_zero_units(self):
        html = "<div style='margin:0px 0.0em; padding:10.0px'>x</div>"
        out = html_slim.normalize_css_values(html)
        self.assertIn("margin:0 0", out)
        self.assertIn("padding:10px", out)

    def test_normalize_style_block(self):
        html = "<style>.a { color: rgb(0, 0, 255); }\n.b { margin: 0px; }\u003c/style>"
        out = html_slim.normalize_css_values(html)
        self.assertIn("color:blue", out)  # rgb(0,0,255) is blue; navy is rgb(0,0,128)
        self.assertIn("margin:0", out)

    # -- compression: shorthand compression -----------------------------------

    def test_compress_padding_uniform(self):
        html = "<div style='padding:10px 10px 10px 10px'>x</div>"
        out = html_slim.compress_shorthands(html)
        self.assertIn("padding:10px", out)

    def test_compress_margin_two_sides(self):
        html = "<div style='margin:0px 5px 0px 5px'>x</div>"
        out = html_slim.compress_shorthands(html)
        self.assertIn("margin:0 5px", out)

    def test_compress_border_longhands(self):
        html = "<div style='border-width:1px;border-style:solid;border-color:#000'>x</div>"
        out = html_slim.compress_shorthands(html)
        self.assertIn("border:1px solid #000", out)

    def test_compress_border_radius(self):
        html = "<div style='border-top-left-radius:4px;border-top-right-radius:4px;border-bottom-right-radius:4px;border-bottom-left-radius:4px'>x</div>"
        out = html_slim.compress_shorthands(html)
        self.assertIn("border-radius:4px", out)

    # -- compression: style-block minification --------------------------------

    def test_minify_style_block(self):
        html = "<style>\n/* comment */\n.a {\n  color: red;\n}\n\u003c/style>"
        out = html_slim.minify_style_blocks(html)
        self.assertNotIn("comment", out)
        self.assertNotIn("\n", out)
        self.assertIn(".a{color:red;}", out)

    # -- compression: strip inherited inline declarations ---------------------

    _SHIPPED = ".o_layout { font-family: Arial,Helvetica Neue,Helvetica,sans-serif; }"

    def test_strip_inherited_drops_matching_font_family(self):
        html = (
            '<div style="font-family:Arial,Helvetica Neue,Helvetica,sans-serif;'
            'color:#333">Hi</div>'
        )
        out = html_slim.strip_inherited_declarations(html, shipped_style_css=self._SHIPPED)
        self.assertNotIn("font-family", out)
        self.assertIn("color:#333", out)  # non-inherited declaration kept

    def test_strip_inherited_handles_computed_quotes_and_spaces(self):
        # getComputedStyle re-quotes multi-word families and adds spaces.
        html = (
            '<td style=\'font-family: Arial, "Helvetica Neue", Helvetica, sans-serif\'>Hi</td>'
        )
        out = html_slim.strip_inherited_declarations(html, shipped_style_css=self._SHIPPED)
        self.assertNotIn("font-family", out)
        self.assertNotIn("style=", out)  # became empty, whole attribute dropped

    def test_strip_inherited_handles_entity_escaped_quotes(self):
        # The serialized style="" attribute escapes inner quotes as &quot;,
        # which end in ';' and must not break declaration splitting.
        html = (
            '<td style="font-family:Arial, &quot;Helvetica Neue&quot;, Helvetica, sans-serif;'
            'color:#454748">Hi</td>'
        )
        out = html_slim.strip_inherited_declarations(html, shipped_style_css=self._SHIPPED)
        self.assertNotIn("font-family", out)
        self.assertNotIn("&quot;", out)
        self.assertIn("color:#454748", out)

    def test_strip_inherited_keeps_different_font(self):
        html = '<div style="font-family:Georgia,serif">Hi</div>'
        out = html_slim.strip_inherited_declarations(html, shipped_style_css=self._SHIPPED)
        self.assertIn("font-family:Georgia,serif", out)

    def test_strip_inherited_noop_without_shipped_css(self):
        html = '<div style="font-family:Arial,Helvetica Neue,Helvetica,sans-serif">Hi</div>'
        self.assertEqual(html_slim.strip_inherited_declarations(html), html)

    def test_strip_inherited_explicit_map(self):
        html = '<div style="color:#3AADAA;font-size:14px">Hi</div>'
        out = html_slim.strip_inherited_declarations(
            html, inherited_map={"color": {"#3aadaa"}}
        )
        self.assertNotIn("color", out)
        self.assertIn("font-size:14px", out)

    def test_strip_inherited_protects_mso_comment(self):
        html = (
            '<!--[if mso]><div style="font-family:Arial,Helvetica Neue,Helvetica,sans-serif">x</div><![endif]-->'
            '<div style="font-family:Arial,Helvetica Neue,Helvetica,sans-serif">y</div>'
        )
        out = html_slim.strip_inherited_declarations(html, shipped_style_css=self._SHIPPED)
        # The declaration inside the [if mso] comment must survive.
        self.assertEqual(out.count("font-family"), 1)
        self.assertIn("[if mso]", out)

    def test_strip_inherited_markupsafe_preserved(self):
        import markupsafe
        out = html_slim.strip_inherited_declarations(
            markupsafe.Markup('<div style="font-family:Arial,Helvetica Neue,Helvetica,sans-serif">x</div>'),
            shipped_style_css=self._SHIPPED,
        )
        self.assertIsInstance(out, markupsafe.Markup)

    # -- pipeline --------------------------------------------------------------

    def test_apply_pipeline_order(self):
        html = (
            '<html><body>'
            '<p class="btn o_layout" style="box-sizing:border-box;padding:10px 10px 10px 10px;color:rgb(255,0,0)">Hello</p>'
            '<img src="https://ex.com/mail/track/5/tok/blank.gif"/>'
            '</body></html>'
        )
        flags = {
            "move_pixel": True,
            "strip_classes": True,
            "trim_defaults": True,
            "normalize_css": True,
            "compress_shorthands": True,
            "minify": True,
        }
        shipped = ".o_layout { width: 100%; }"
        out = html_slim.apply_pipeline(html, flags, shipped_style_css=shipped)
        self.assertLess(out.index("/mail/track/"), out.index("Hello"))
        self.assertIn('o_layout', out)
        self.assertNotIn('btn', out)
        self.assertNotIn("box-sizing", out)
        self.assertIn("padding:10px", out)
        self.assertIn("color:red", out)
        self.assertNotIn("\n", out)

    def test_idempotency_of_full_pipeline(self):
        html = (
            "<html>\n"
            "  <body style='box-sizing:border-box' class='o_layout'>\n"
            "    <p class='btn text-center' style='padding:10px 10px 10px 10px;color:rgb(255,0,0)'>Hello</p>\n"
            "    <img src='https://ex.com/mail/track/5/tok/blank.gif'/>\n"
            "  </body>\n"
            "</html>"
        )
        flags = {
            "move_pixel": True,
            "strip_classes": True,
            "trim_defaults": True,
            "normalize_css": True,
            "compress_shorthands": True,
            "minify_style_blocks": True,
            "minify": True,
        }
        shipped = ".o_layout { width: 100%; } .text-center { text-align: center; }"
        once = html_slim.apply_pipeline(html, flags, shipped_style_css=shipped)
        twice = html_slim.apply_pipeline(once, flags, shipped_style_css=shipped)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("/mail/track/"), 1)
        self.assertIn("Hello", once)
        self.assertNotIn("box-sizing", once)

    # -- diagnostic ------------------------------------------------------------

    def test_diagnose_bloat(self):
        html = "<div class='btn o_layout' style='color:red'>Hi</div>"
        report = html_slim.diagnose_bloat(html)
        self.assertEqual(report["total_bytes"], len(html.encode("utf-8")))
        self.assertEqual(report["inline_style_bytes"], len("color:red"))
        self.assertGreater(report["class_attr_bytes"], 0)
        self.assertIn("btn", [t for t, _ in report["top_classes"]])

    # -- markup type preservation ---------------------------------------------

    def test_markupsafe_type_preserved(self):
        import markupsafe
        out = html_slim.minify_email_html(markupsafe.Markup("<a>\n<b>x</b>\n</a>"))
        self.assertIsInstance(out, markupsafe.Markup)
        out = html_slim.strip_dead_classes(markupsafe.Markup("<div class='btn'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)
        out = html_slim.trim_redundant_inline_defaults(markupsafe.Markup("<div style='box-sizing:border-box'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)
        out = html_slim.normalize_css_values(markupsafe.Markup("<div style='color:rgb(255,0,0)'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)
        out = html_slim.compress_shorthands(markupsafe.Markup("<div style='padding:10px 10px 10px 10px'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)
