# Part of the booksandco custom addons. See LICENSE.
from odoo import models
from odoo.tools.misc import file_open

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
            "normalize_css": icp.get_param("mass_mailing_slim.normalize_css", "False") == "True",
            "compress_shorthands": icp.get_param("mass_mailing_slim.compress_shorthands", "False") == "True",
            "minify_style_blocks": icp.get_param("mass_mailing_slim.minify_style_blocks", "False") == "True",
        }

    def _mass_mailing_slim_allowlist(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "mass_mailing_slim.class_allowlist", ""
        )
        return tuple(c.strip() for c in raw.split(",") if c.strip()) or html_slim.DEFAULT_CLASS_ALLOWLIST

    def _mass_mailing_slim_shipped_css(self):
        """Return the CSS text that ships with every email.

        This is the same file mass_mailing injects into the <head> of outgoing
        mails (see wizard/mail_compose_message.py and
        wizard/mailing_mailing_test.py). Passing it to strip_dead_classes lets
        us keep only classes referenced by CSS that actually travels with the
        email, instead of classes referenced by editor-only/injected styles.
        """
        try:
            with file_open("mass_mailing/static/src/scss/mass_mailing_mail.scss", "r") as fd:
                return fd.read()
        except Exception:
            return None

    def _prepare_outgoing_body(self):
        # super() (mass_mailing) already appends the open-tracking pixel at the
        # end of the body; we post-process the fully wrapped HTML here.
        body = super()._prepare_outgoing_body()
        if not body or not self.mailing_id:
            return body
        flags = self._mass_mailing_slim_flags()
        if not flags:
            return body
        return html_slim.apply_pipeline(
            body,
            flags,
            allowlist=self._mass_mailing_slim_allowlist(),
            shipped_style_css=self._mass_mailing_slim_shipped_css(),
        )

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

    def _mass_mailing_slim_diagnose(self):
        """Log a bloat breakdown for this outgoing mailing."""
        body = self.body_html or ""
        if not body or not self.mailing_id:
            return
        report = html_slim.diagnose_bloat(body)
        # Deliberately not using _logger here to avoid noise; this can be called
        # manually or from a debug action.
        return report
