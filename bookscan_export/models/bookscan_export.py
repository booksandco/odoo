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

    def _get_tz(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'bookscan_export.timezone', 'Pacific/Auckland'
        )

    @api.model
    def _get_pos_sales(self, date_from, date_to):
        """Query POS order lines for book sales in the date range.

        Returns a list of dicts ready for CSV rows.
        """
        tz = self._get_tz()
        self.env.cr.execute("""
            SELECT
                pp.barcode                          AS isbn,
                pol.qty                             AS qty,
                pol.price_unit                      AS price,
                po.date_order AT TIME ZONE %s       AS sale_date
            FROM pos_order_line pol
            JOIN pos_order      po  ON po.id = pol.order_id
            JOIN product_product pp ON pp.id = pol.product_id
            WHERE po.state IN ('paid', 'done')
              AND (po.date_order AT TIME ZONE %s)::date >= %s
              AND (po.date_order AT TIME ZONE %s)::date <= %s
              AND pp.barcode IS NOT NULL
              AND pp.barcode ~ '^97[89]'
            ORDER BY po.date_order
        """, (tz, tz, date_from, tz, date_to))
        return self.env.cr.dictfetchall()

    @api.model
    def _get_sale_order_sales(self, date_from, date_to):
        """Query confirmed sale order lines for books in the date range."""
        tz = self._get_tz()
        self.env.cr.execute("""
            SELECT
                pp.barcode                          AS isbn,
                sol.product_uom_qty                 AS qty,
                sol.price_unit                      AS price,
                so.date_order AT TIME ZONE %s       AS sale_date,
                CASE WHEN dc.name::text ILIKE '%%collect%%' THEN NULL ELSE rp.zip END AS postcode,
                CASE WHEN dc.name::text ILIKE '%%collect%%' THEN NULL ELSE rc.code END AS country_code
            FROM sale_order_line    sol
            JOIN sale_order         so  ON so.id = sol.order_id
            JOIN product_product   pp  ON pp.id = sol.product_id
            LEFT JOIN delivery_carrier dc ON dc.id = so.carrier_id
            LEFT JOIN res_partner  rp  ON rp.id = so.partner_shipping_id
            LEFT JOIN res_country  rc  ON rc.id = rp.country_id
            WHERE so.state IN ('sale', 'done')
              AND (so.date_order AT TIME ZONE %s)::date >= %s
              AND (so.date_order AT TIME ZONE %s)::date <= %s
              AND pp.barcode IS NOT NULL
              AND pp.barcode ~ '^97[89]'
            ORDER BY so.date_order
        """, (tz, tz, date_from, tz, date_to))
        return self.env.cr.dictfetchall()

    @api.model
    def _build_csv(self, rows, store_id):
        """Build a BookScan-format CSV string from sale rows."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'Identifier', 'PLI', 'ISO', 'Product Code',
            'Quantity', 'Actual Selling Price', 'Sale Date',
        ])
        for row in rows:
            sale_date = row['sale_date'].strftime('%Y%m%d')
            qty = int(row['qty'])
            price = f"{row['price']:.2f}"
            postcode = row.get('postcode') or ''
            country_code = row.get('country_code') or ''

            writer.writerow([
                store_id,
                postcode,
                country_code,
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

        if not host or not username:
            raise UserError(_('BookScan SFTP is not configured. Go to Settings > Point of Sale > BookScan Export.'))

        transport = paramiko.Transport((host, port))
        try:
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
        """Scheduled action: export the prior Sunday–Saturday week to BookScan."""
        today = fields.Date.context_today(self)
        days_since_sunday = (today.weekday() + 1) % 7
        date_to = today - timedelta(days=days_since_sunday + 1)
        date_from = date_to - timedelta(days=6)

        self._run_export(date_from, date_to)

    @api.model
    def _run_export(self, date_from, date_to):
        """Generate CSV and upload for the given date range."""
        config = self.env['ir.config_parameter'].sudo()
        outlet_name = config.get_param('bookscan_export.outlet_name', 'booksandco')
        store_id = config.get_param('bookscan_export.store_id', 'bco0001')

        pos_rows = self._get_pos_sales(date_from, date_to)
        sale_rows = self._get_sale_order_sales(date_from, date_to)
        all_rows = pos_rows + sale_rows

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

        csv_content = self._build_csv(all_rows, store_id)

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
