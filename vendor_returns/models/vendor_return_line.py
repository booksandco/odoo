from odoo import _, fields, models, tools
from odoo.exceptions import UserError


class VendorReturnLine(models.Model):
    _name = 'vendor.return.line'
    _description = 'Vendor Return Planning Line'
    _auto = False
    _order = 'receipt_date asc, vendor_id'

    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    barcode = fields.Char(related='product_id.barcode', string='ISBN')
    vendor_id = fields.Many2one('res.partner', string='Vendor', readonly=True)
    source_move_id = fields.Many2one('stock.move', string='Source Receipt', readonly=True)
    receipt_date = fields.Date('Receipt Date', readonly=True)
    remaining_qty = fields.Float('Returnable Qty', readonly=True)
    age_days = fields.Integer('Age (Days)', readonly=True)
    return_window_end = fields.Date('Window End', readonly=True)
    window_status = fields.Selection([
        ('too_early', 'Too Early'),
        ('within_window', 'Returnable'),
        ('expired', 'Expired'),
        ('no_policy', 'No Policy'),
    ], string='Status', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH stock AS (
                    SELECT sq.product_id, sq.company_id, SUM(sq.quantity) AS on_hand_qty
                    FROM stock_quant sq
                    JOIN stock_location sl ON sl.id = sq.location_id
                    WHERE sl.usage = 'internal'
                    GROUP BY sq.product_id, sq.company_id
                    HAVING SUM(sq.quantity) > 0
                ),
                receipts AS (
                    SELECT
                        sm.id AS move_id,
                        sm.product_id,
                        sm.company_id,
                        sm.purchase_line_id,
                        sp.partner_id AS vendor_id,
                        sm.date::date AS receipt_date,
                        sm.quantity AS receipt_qty,
                        SUM(sm.quantity) OVER (
                            PARTITION BY sm.product_id, sm.company_id
                            ORDER BY sm.date DESC, sm.id DESC
                        ) AS cumulative_from_newest
                    FROM stock_move sm
                    JOIN stock_picking sp ON sp.id = sm.picking_id
                    JOIN stock_location src ON src.id = sm.location_id
                    JOIN stock_location dest ON dest.id = sm.location_dest_id
                    WHERE sm.state = 'done'
                      AND src.usage = 'supplier'
                      AND dest.usage = 'internal'
                ),
                bill_dates AS (
                    SELECT DISTINCT ON (aml.purchase_line_id)
                        aml.purchase_line_id,
                        am.invoice_date
                    FROM account_move_line aml
                    JOIN account_move am ON am.id = aml.move_id
                    WHERE aml.purchase_line_id IS NOT NULL
                      AND am.move_type = 'in_invoice'
                      AND am.state = 'posted'
                    ORDER BY aml.purchase_line_id, am.invoice_date ASC
                )
                SELECT
                    r.move_id AS id,
                    r.product_id,
                    r.vendor_id,
                    r.move_id AS source_move_id,
                    r.receipt_date,
                    GREATEST(0, LEAST(
                        r.receipt_qty,
                        s.on_hand_qty - (r.cumulative_from_newest - r.receipt_qty)
                    )) AS remaining_qty,
                    CURRENT_DATE - r.receipt_date AS age_days,
                    CASE
                        WHEN COALESCE(rp.return_max_days, 0) = 0 THEN NULL
                        WHEN COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication'
                                 THEN pt.x_publication_date END,
                            CASE WHEN rp.return_date_basis = 'invoice'
                                 THEN bd.invoice_date END,
                            r.receipt_date
                        ) IS NULL THEN NULL
                        ELSE COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication'
                                 THEN pt.x_publication_date END,
                            CASE WHEN rp.return_date_basis = 'invoice'
                                 THEN bd.invoice_date END,
                            r.receipt_date
                        ) + rp.return_max_days
                    END AS return_window_end,
                    CASE
                        WHEN COALESCE(rp.return_max_days, 0) = 0 THEN 'no_policy'
                        WHEN COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication'
                                 THEN pt.x_publication_date END,
                            CASE WHEN rp.return_date_basis = 'invoice'
                                 THEN bd.invoice_date END,
                            r.receipt_date
                        ) IS NULL THEN 'no_policy'
                        WHEN CURRENT_DATE < COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication'
                                 THEN pt.x_publication_date END,
                            CASE WHEN rp.return_date_basis = 'invoice'
                                 THEN bd.invoice_date END,
                            r.receipt_date
                        ) + COALESCE(rp.return_min_days, 0) THEN 'too_early'
                        WHEN CURRENT_DATE <= COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication'
                                 THEN pt.x_publication_date END,
                            CASE WHEN rp.return_date_basis = 'invoice'
                                 THEN bd.invoice_date END,
                            r.receipt_date
                        ) + rp.return_max_days THEN 'within_window'
                        ELSE 'expired'
                    END AS window_status,
                    r.company_id
                FROM receipts r
                JOIN stock s ON s.product_id = r.product_id
                    AND s.company_id = r.company_id
                JOIN product_product pp ON pp.id = r.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN bill_dates bd ON bd.purchase_line_id = r.purchase_line_id
                LEFT JOIN res_partner rp ON rp.id = r.vendor_id
                WHERE GREATEST(0, LEAST(
                    r.receipt_qty,
                    s.on_hand_qty - (r.cumulative_from_newest - r.receipt_qty)
                )) > 0
            )
        """ % self._table)

    def action_add_to_return_order(self):
        """Add selected lines to a vendor return order (one per vendor)."""
        lines = self.browse(self.env.context.get('active_ids', []))
        if not lines:
            raise UserError(_("Please select at least one line."))

        ReturnOrder = self.env['vendor.return.order']
        orders = self.env['vendor.return.order']

        for vendor in lines.vendor_id:
            vendor_lines = lines.filtered(lambda l: l.vendor_id == vendor)
            order = ReturnOrder.search([
                ('partner_id', '=', vendor.id),
                ('state', '=', 'draft'),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
            if not order:
                order = ReturnOrder.create({'partner_id': vendor.id})
            for line in vendor_lines:
                existing = order.order_line.filtered(
                    lambda l: l.source_move_id.id == line.source_move_id.id
                )
                if not existing:
                    source = self.env['stock.move'].browse(line.source_move_id.id)
                    price = source.purchase_line_id.price_unit if source.purchase_line_id else 0.0
                    self.env['vendor.return.order.line'].create({
                        'order_id': order.id,
                        'product_id': line.product_id.id,
                        'product_qty': line.remaining_qty,
                        'product_uom': source.product_uom.id,
                        'price_unit': price,
                        'source_move_id': line.source_move_id.id,
                    })
            orders |= order

        if len(orders) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Vendor Return Order'),
                'res_model': 'vendor.return.order',
                'res_id': orders.id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Return Orders'),
            'res_model': 'vendor.return.order',
            'domain': [('id', 'in', orders.ids)],
            'view_mode': 'list,form',
        }
