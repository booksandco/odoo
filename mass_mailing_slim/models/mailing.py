# Part of the booksandco custom addons. See LICENSE.
import logging

from odoo import api, fields, models

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

    @api.depends("body_html")
    def _compute_email_size_kb(self):
        icp = self.env["ir.config_parameter"].sudo()
        warn_kb = float(icp.get_param("mass_mailing_slim.warn_kb", 100) or 100)
        enabled = icp.get_param("mass_mailing_slim.enabled", "True") == "True"

        for mailing in self:
            body = mailing.body_html or ""
            if enabled:
                body = html_slim.apply_pipeline(
                    body,
                    {
                        "minify": icp.get_param("mass_mailing_slim.minify", "True") == "True",
                        "move_pixel": icp.get_param("mass_mailing_slim.move_pixel", "True") == "True",
                        "strip_classes": icp.get_param("mass_mailing_slim.strip_classes", "False") == "True",
                        "trim_defaults": icp.get_param("mass_mailing_slim.trim_defaults", "False") == "True",
                    },
                    allowlist=tuple(
                        c.strip()
                        for c in icp.get_param("mass_mailing_slim.class_allowlist", "").split(",")
                        if c.strip()
                    )
                    or html_slim.DEFAULT_CLASS_ALLOWLIST,
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
