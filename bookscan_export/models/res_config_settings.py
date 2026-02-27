import base64
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

    def _bookscan_export_date_range(self):
        today = fields.Date.context_today(self)
        return today - timedelta(days=7), today - timedelta(days=1)

    def action_bookscan_download_csv(self):
        """Generate CSV and download it for review."""
        date_from, date_to = self._bookscan_export_date_range()

        export_model = self.env['bookscan.export.log']
        config = self.env['ir.config_parameter'].sudo()
        outlet_name = config.get_param('bookscan_export.outlet_name', 'booksandco')
        filename = f"{outlet_name}{date_to.strftime('%Y%m%d')}.csv"

        pos_rows = export_model._get_pos_sales(date_from, date_to)
        web_rows = export_model._get_website_sales(date_from, date_to)
        all_rows = pos_rows + web_rows

        csv_content = export_model._build_csv(all_rows) if all_rows else ''

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_content.encode('utf-8')),
            'mimetype': 'text/csv',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_bookscan_upload_now(self):
        """Generate CSV and upload to BookScan SFTP."""
        date_from, date_to = self._bookscan_export_date_range()
        self.env['bookscan.export.log']._run_export(date_from, date_to)
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
