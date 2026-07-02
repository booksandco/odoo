# Part of the booksandco custom addons. See LICENSE.
from odoo import models

from odoo.addons.mass_mailing_slim.tools import html_slim


class MailMail(models.Model):
    """Post-process outgoing mailing HTML to keep it under Gmail's clip limit."""

    _inherit = "mail.mail"

    def _mass_mailing_slim_flags(self):
        """Return the enabled slim transforms, or ``{}`` when disabled."""
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("mass_mailing_slim.enabled", "True") != "True":
            return {}
        return {
            "minify": icp.get_param("mass_mailing_slim.minify", "True") == "True",
            "fix_plaintext": icp.get_param("mass_mailing_slim.fix_plaintext", "True") == "True",
            "move_pixel": icp.get_param("mass_mailing_slim.move_pixel", "True") == "True",
            "strip_classes": icp.get_param("mass_mailing_slim.strip_classes", "False") == "True",
            "trim_defaults": icp.get_param("mass_mailing_slim.trim_defaults", "False") == "True",
        }

    def _mass_mailing_slim_allowlist(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "mass_mailing_slim.class_allowlist", ""
        )
        return tuple(c.strip() for c in raw.split(",") if c.strip()) or html_slim.DEFAULT_CLASS_ALLOWLIST

    def _prepare_outgoing_body(self):
        # super() (mass_mailing) already appends the open-tracking pixel at the
        # end of the body; we post-process the fully wrapped HTML here.
        body = super()._prepare_outgoing_body()
        if not body or not self.mailing_id:
            return body
        flags = self._mass_mailing_slim_flags()
        if not flags:
            return body
        if flags["move_pixel"]:
            body = html_slim.relocate_tracking_pixel(body)
        if flags["strip_classes"]:  # aggressive tier (currently a no-op stub)
            body = html_slim.strip_dead_classes(body, allowlist=self._mass_mailing_slim_allowlist())
        if flags["trim_defaults"]:  # aggressive tier (currently a no-op stub)
            body = html_slim.trim_redundant_inline_defaults(body)
        if flags["minify"]:
            body = html_slim.minify_email_html(body)
        return body

    def _prepare_outgoing_list(self, mail_server=False, doc_to_followers=None):
        email_list = super()._prepare_outgoing_list(
            mail_server=mail_server, doc_to_followers=doc_to_followers
        )
        if not self.mailing_id:
            return email_list
        flags = self._mass_mailing_slim_flags()
        if not flags or not flags.get("fix_plaintext"):
            return email_list
        # Rebuild the text/plain alternative without leaking CSS from <style> blocks.
        for vals in email_list:
            if vals.get("body"):
                vals["body_alternative"] = html_slim.html_to_text_no_css(vals["body"])
        return email_list
