from datetime import timedelta

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    bookscan_sftp_host = fields.Char(
        string="SFTP Host",
        config_parameter='bookscan_export.sftp_host',
    )
    bookscan_sftp_port = fields.Integer(
        string="SFTP Port",
        config_parameter='bookscan_export.sftp_port',
        default=22,
    )
    bookscan_sftp_username = fields.Char(
        string="SFTP Username",
        config_parameter='bookscan_export.sftp_username',
    )
    bookscan_sftp_password = fields.Char(
        string="SFTP Password",
        config_parameter='bookscan_export.sftp_password',
    )
    bookscan_sftp_key_path = fields.Char(
        string="SFTP Private Key Path",
        config_parameter='bookscan_export.sftp_key_path',
        help="Absolute path to your SSH private key file on the server. Leave blank to use password auth.",
    )
    bookscan_outlet_name = fields.Char(
        string="Outlet Name",
        config_parameter='bookscan_export.outlet_name',
        default='booksandco',
        help="Used in the export filename, e.g. booksandco20260227.csv",
    )

    def action_bookscan_export_now(self):
        """Manual export: yesterday's sales."""
        today = fields.Date.context_today(self)
        date_to = today - timedelta(days=1)
        self.env['bookscan.export.log']._run_export(date_to, date_to)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'BookScan Export',
                'message': 'Export completed – check the log for details.',
                'type': 'success',
                'sticky': False,
            },
        }
