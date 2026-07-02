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

    # -- combined + edge cases ------------------------------------------------

    def test_aggressive_preserves_mso_markup(self):
        html = (
            "<!--[if mso]><style>.outlook { width: 600px; }\u003c/style>"
            "<v:rect style='width:600px' class='outlook mso_class'/><![endif]-->"
            "<div class='btn'>hi</div>"
        )
        out = html_slim.strip_dead_classes(html)
        self.assertIn("[if mso]", out)
        self.assertIn("outlook", out)
        self.assertNotIn("btn", out)

    def test_style_content_unchanged_by_trim(self):
        html = (
            "<style>\n  .a { box-sizing: border-box; border-radius: 0px; }\n"
            "  .b { color: red; }\n</style>"
            "<div class='a b' style='box-sizing:border-box;border-radius:0px;color:red'>hi</div>"
        )
        out = html_slim.trim_redundant_inline_defaults(
            html_slim.strip_dead_classes(html)
        )
        self.assertIn("box-sizing: border-box", out)  # inside <style>
        self.assertIn("border-radius: 0px", out)
        self.assertNotIn("style=", out)  # remaining inline style was only no-ops
        self.assertIn('a', out)
        self.assertIn('b', out)

    def test_minify_with_markup_preserving_classes(self):
        html = (
            "<style>.kept { color: red; }\u003c/style>\n"
            "<div  class=\"btn  kept\"  \u003e\n  Hello world\n\u003c/div\u003e"
        )
        out = html_slim.minify_email_html(
            html_slim.strip_dead_classes(html)
        )
        self.assertIn('kept', out)
        self.assertNotIn('btn', out)
        self.assertIn("Hello world", out)
        self.assertNotIn("\n", out)

    def test_idempotency_of_combined_safe_pipeline(self):
        html = (
            "<html>\n"
            "  <body style='box-sizing:border-box'\u003e\n"
            "    <p class='btn text-center'>Hello\u003c/p>\n"
            "    <img src='https://ex.com/mail/track/5/tok/blank.gif'/>\n"
            "  </body>\n"
            "</html>"
        )
        once = html_slim.minify_email_html(
            html_slim.trim_redundant_inline_defaults(
                html_slim.strip_dead_classes(
                    html_slim.relocate_tracking_pixel(html)
                )
            )
        )
        twice = html_slim.minify_email_html(
            html_slim.trim_redundant_inline_defaults(
                html_slim.strip_dead_classes(
                    html_slim.relocate_tracking_pixel(once)
                )
            )
        )
        self.assertEqual(once, twice)
        self.assertEqual(once.count("/mail/track/"), 1)
        self.assertIn("Hello", once)
        self.assertNotIn("box-sizing", once)
        self.assertNotIn("btn", once)

    def test_markupsafe_type_preserved(self):
        import markupsafe
        out = html_slim.minify_email_html(markupsafe.Markup("<a>\n<b>x</b>\n</a>"))
        self.assertIsInstance(out, markupsafe.Markup)

        out = html_slim.strip_dead_classes(markupsafe.Markup("<div class='btn'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)

        out = html_slim.trim_redundant_inline_defaults(markupsafe.Markup("<div style='box-sizing:border-box'>x</div>"))
        self.assertIsInstance(out, markupsafe.Markup)
