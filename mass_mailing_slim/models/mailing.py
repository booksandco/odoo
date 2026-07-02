# Part of the booksandco custom addons. See LICENSE.
import logging

from odoo import api, fields, models
from odoo.tools.misc import file_open

from odoo.addons.mass_mailing_slim.tools import html_slim

_logger = logging.getLogger(__name__)


class MailingMailing(models.Model):
    """Surface an estimated email size + a Gmail-clip warning in the editor."""

    _inherit = "mailing.mailing"

    email_size_kb = fields.Float(
        string="Estimated Email Size (KB)",
        compute="_compute_email_size_kb",
        help="Approximate size of the final HTML body after slimming. Gmail clips emails over ~102 KB.",
    )
    email_size_warning = fields.Boolean(compute="_compute_email_size_kb")

    def _mass_mailing_slim_flags(self):
        """Return active slim flags (mirror of mail.mail)."""
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("mass_mailing_slim.enabled", "True") != "True":
            return {}
        return {
            "minify": icp.get_param("mass_mailing_slim.minify", "True") == "True",
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
        try:
            with file_open("mass_mailing/static/src/scss/mass_mailing_mail.scss", "r") as fd:
                return fd.read()
        except Exception:
            return None

    @api.depends("body_html")
    def _compute_email_size_kb(self):
        icp = self.env["ir.config_parameter"].sudo()
        warn_kb = float(icp.get_param("mass_mailing_slim.warn_kb", 100) or 100)
        flags = self._mass_mailing_slim_flags()
        allowlist = self._mass_mailing_slim_allowlist()
        shipped_css = self._mass_mailing_slim_shipped_css()

        for mailing in self:
            body = mailing.body_html or ""
            if flags:
                body = html_slim.apply_pipeline(
                    body,
                    flags,
                    allowlist=allowlist,
                    shipped_style_css=shipped_css,
                )
            size_kb = len(body.encode("utf-8")) / 1024.0
            mailing.email_size_kb = round(size_kb, 1)
            mailing.email_size_warning = size_kb >= warn_kb

    def _action_send_mail(self, res_ids=None):
        for mailing in self:
            if mailing.email_size_warning:
                _logger.warning(
                    "mass_mailing_slim: mailing %s (%s) HTML body is ~%.0f KB; "
                    "Gmail clips emails over ~102 KB, which hides content and can "
                    "break open tracking.",
                    mailing.id,
                    mailing.subject,
                    mailing.email_size_kb,
                )
        return super()._action_send_mail(res_ids=res_ids)

    def action_mass_mailing_slim_diagnose(self):
        """Debug action: log a bloat breakdown for this mailing."""
        self.ensure_one()
        report = html_slim.diagnose_bloat(self.body_html or "")
        _logger.info(
            "mass_mailing_slim diagnose for mailing %s (%s): total=%s bytes, "
            "inline_styles=%s bytes, classes=%s bytes, style_blocks=%s bytes, "
            "top_styles=%s, top_classes=%s",
            self.id,
            self.subject,
            report["total_bytes"],
            report["inline_style_bytes"],
            report["class_attr_bytes"],
            report["style_block_bytes"],
            report["top_inline_styles"][:5],
            report["top_classes"][:5],
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": "Slim Diagnose",
                "message": (
                    f"Total: {report['total_bytes']:,} bytes | "
                    f"inline styles: {report['inline_style_bytes']:,} | "
                    f"classes: {report['class_attr_bytes']:,} | "
                    f"style blocks: {report['style_block_bytes']:,}"
                ),
                "sticky": True,
            },
        }
