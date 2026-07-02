# Part of the booksandco custom addons. See LICENSE.
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MailingMailing(models.Model):
    """Surface an estimated email size + a Gmail-clip warning in the editor."""

    _inherit = "mailing.mailing"

    email_size_kb = fields.Float(
        string="Estimated Email Size (KB)",
        compute="_compute_email_size_kb",
        help="Approximate size of the HTML body. Gmail clips emails over ~102 KB.",
    )
    email_size_warning = fields.Boolean(compute="_compute_email_size_kb")

    @api.depends("body_html")
    def _compute_email_size_kb(self):
        warn_kb = float(
            self.env["ir.config_parameter"].sudo().get_param("mass_mailing_slim.warn_kb", 100)
            or 100
        )
        for mailing in self:
            body = mailing.body_html or ""
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
