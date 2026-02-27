import csv
import io
import logging
import tempfile
from datetime import timedelta

import paramiko

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BookscanExportLog(models.Model):
    _name = 'bookscan.export.log'
    _description = 'BookScan Export Log'
    _order = 'export_date desc'

    export_date = fields.Datetime(string='Export Date', default=fields.Datetime.now, readonly=True)
    date_from = fields.Date(string='From', readonly=True)
    date_to = fields.Date(string='To', readonly=True)
    filename = fields.Char(string='Filename', readonly=True)
    record_count = fields.Integer(string='Records', readonly=True)
    state = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
    ], string='Status', readonly=True)
    error_message = fields.Text(string='Error', readonly=True)

    # ---- CSV generation ----

    @api.model
    def _get_pos_sales(self, date_from, date_to):
        """Query POS order lines for book sales in the date range.

        Returns a list of dicts ready for CSV rows.
        """
        self.env.cr.execute("""
            SELECT
                pc.name                             AS outlet,
                pt.barcode                          AS isbn,
                pol.qty                             AS qty,
                pol.price_unit                      AS price,
                po.date_order                       AS sale_date,
                rp.zip                              AS postcode,
                rc.code                             AS country_code
            FROM pos_order_line pol
            JOIN pos_order      po  ON po.id = pol.order_id
            JOIN pos_config     pc  ON pc.id = po.config_id
            JOIN product_product pp ON pp.id = pol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN res_partner rp ON rp.id = po.partner_id
            LEFT JOIN res_country rc ON rc.id = rp.country_id
            WHERE po.state IN ('paid', 'done')
              AND po.date_order::date >= %s
              AND po.date_order::date <= %s
              AND pt.barcode IS NOT NULL
              AND pt.barcode ~ '^97[89]'
            ORDER BY po.date_order
        """, (date_from, date_to))
        return self.env.cr.dictfetchall()

    @api.model
    def _get_website_sales(self, date_from, date_to):
        """Query confirmed website sale order lines for books in the date range."""
        self.env.cr.execute("""
            SELECT
                'onlinestore'                       AS outlet,
                pt.barcode                          AS isbn,
                sol.product_uom_qty                 AS qty,
                sol.price_unit                      AS price,
                so.date_order                       AS sale_date,
                rp.zip                              AS postcode,
                rc.code                             AS country_code
            FROM sale_order_line    sol
            JOIN sale_order         so  ON so.id = sol.order_id
            JOIN product_product   pp  ON pp.id = sol.product_id
            JOIN product_template  pt  ON pt.id = pp.product_tmpl_id
            LEFT JOIN res_partner  rp  ON rp.id = so.partner_shipping_id
            LEFT JOIN res_country  rc  ON rc.id = rp.country_id
            WHERE so.state IN ('sale', 'done')
              AND so.website_id IS NOT NULL
              AND so.date_order::date >= %s
              AND so.date_order::date <= %s
              AND pt.barcode IS NOT NULL
              AND pt.barcode ~ '^97[89]'
            ORDER BY so.date_order
        """, (date_from, date_to))
        return self.env.cr.dictfetchall()

    @api.model
    def _build_csv(self, rows):
        """Build a BookScan-format CSV string from sale rows."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in rows:
            sale_date = row['sale_date'].strftime('%Y%m%d')
            qty = int(row['qty'])
            price = f"{row['price']:.2f}"

            if row.get('postcode') and row.get('country_code'):
                writer.writerow([
                    row['outlet'],
                    row['postcode'],
                    row['country_code'],
                    row['isbn'],
                    qty,
                    price,
                    sale_date,
                ])
            else:
                writer.writerow([
                    row['outlet'],
                    row['isbn'],
                    qty,
                    price,
                    sale_date,
                ])
        return buf.getvalue()

    # ---- SFTP upload ----

    @api.model
    def _sftp_upload(self, filename, content):
        """Upload CSV content to Nielsen BookScan SFTP server."""
        config = self.env['ir.config_parameter'].sudo()
        host = config.get_param('bookscan_export.sftp_host', '')
        port = int(config.get_param('bookscan_export.sftp_port', '22'))
        username = config.get_param('bookscan_export.sftp_username', '')
        password = config.get_param('bookscan_export.sftp_password', '')
        key_path = config.get_param('bookscan_export.sftp_key_path', '')

        if not host or not username:
            raise UserError(_('BookScan SFTP is not configured. Go to Settings > Point of Sale > BookScan Export.'))

        transport = paramiko.Transport((host, port))
        try:
            if key_path:
                pkey = paramiko.RSAKey.from_private_key_file(key_path)
                transport.connect(username=username, pkey=pkey)
            else:
                transport.connect(username=username, password=password)

            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp:
                    tmp.write(content)
                    tmp.flush()
                    sftp.put(tmp.name, filename)
                _logger.info("BookScan: uploaded %s to %s", filename, host)
            finally:
                sftp.close()
        finally:
            transport.close()

    # ---- Main entry point ----

    @api.model
    def _cron_export(self):
        """Scheduled action: export yesterday's sales to BookScan."""
        today = fields.Date.context_today(self)
        date_to = today - timedelta(days=1)
        date_from = date_to  # single day

        self._run_export(date_from, date_to)

    @api.model
    def _run_export(self, date_from, date_to):
        """Generate CSV and upload for the given date range."""
        config = self.env['ir.config_parameter'].sudo()
        outlet_name = config.get_param('bookscan_export.outlet_name', 'booksandco')

        pos_rows = self._get_pos_sales(date_from, date_to)
        web_rows = self._get_website_sales(date_from, date_to)
        all_rows = pos_rows + web_rows

        filename = f"{outlet_name}{date_to.strftime('%Y%m%d')}.csv"

        if not all_rows:
            _logger.info("BookScan: no book sales for %s – %s, skipping upload.", date_from, date_to)
            self.create({
                'date_from': date_from,
                'date_to': date_to,
                'filename': filename,
                'record_count': 0,
                'state': 'success',
            })
            return

        csv_content = self._build_csv(all_rows)

        try:
            self._sftp_upload(filename, csv_content)
            self.create({
                'date_from': date_from,
                'date_to': date_to,
                'filename': filename,
                'record_count': len(all_rows),
                'state': 'success',
            })
        except Exception as e:
            _logger.exception("BookScan export failed")
            self.create({
                'date_from': date_from,
                'date_to': date_to,
                'filename': filename,
                'record_count': len(all_rows),
                'state': 'error',
                'error_message': str(e),
            })
