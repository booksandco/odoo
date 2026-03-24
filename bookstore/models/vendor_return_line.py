from odoo import _, fields, models, tools
from odoo.exceptions import UserError


class VendorReturnLine(models.Model):
    _name = 'vendor.return.line'
    _description = 'Vendor Return Planning Line'
    _auto = False
    _order = 'invoice_date asc, vendor_id'

    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    barcode = fields.Char(related='product_id.barcode', string='ISBN')
    vendor_id = fields.Many2one('res.partner', string='Vendor', readonly=True)
    on_hand_qty = fields.Float('On Hand', readonly=True)
    invoice_date = fields.Date('Invoice Date', readonly=True)
    age_days = fields.Integer('Age (Days)', readonly=True)
    return_window_end = fields.Date('Window End', readonly=True)
    window_status = fields.Selection([
        ('too_early', 'Too Early'),
        ('within_window', 'Returnable'),
        ('expired', 'Expired'),
        ('no_policy', 'No Policy'),
    ], string='Status', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH stock AS (
                    SELECT sq.product_id, SUM(sq.quantity) AS on_hand_qty
                    FROM stock_quant sq
                    JOIN stock_location sl ON sl.id = sq.location_id
                    WHERE sl.usage = 'internal'
                    GROUP BY sq.product_id
                    HAVING SUM(sq.quantity) > 0
                ),
                receipts AS (
                    SELECT sm.product_id, MIN(sm.date)::date AS invoice_date
                    FROM stock_move sm
                    JOIN stock_location src ON src.id = sm.location_id
                    JOIN stock_location dest ON dest.id = sm.location_dest_id
                    WHERE sm.state = 'done'
                      AND src.usage = 'supplier'
                      AND dest.usage = 'internal'
                    GROUP BY sm.product_id
                )
                SELECT
                    pp.id AS id,
                    pp.id AS product_id,
                    vendor.partner_id AS vendor_id,
                    s.on_hand_qty,
                    r.invoice_date,
                    CASE WHEN r.invoice_date IS NOT NULL
                        THEN CURRENT_DATE - r.invoice_date
                        ELSE 0
                    END AS age_days,
                    CASE
                        WHEN COALESCE(rp.return_max_days, 0) = 0 THEN NULL
                        WHEN COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication' THEN pt.x_publication_date END,
                            r.invoice_date
                        ) IS NULL THEN NULL
                        ELSE COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication' THEN pt.x_publication_date END,
                            r.invoice_date
                        ) + rp.return_max_days
                    END AS return_window_end,
                    CASE
                        WHEN COALESCE(rp.return_max_days, 0) = 0 THEN 'no_policy'
                        WHEN COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication' THEN pt.x_publication_date END,
                            r.invoice_date
                        ) IS NULL THEN 'no_policy'
                        WHEN CURRENT_DATE < COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication' THEN pt.x_publication_date END,
                            r.invoice_date
                        ) + COALESCE(rp.return_min_days, 0) THEN 'too_early'
                        WHEN CURRENT_DATE <= COALESCE(
                            CASE WHEN rp.return_date_basis = 'publication' THEN pt.x_publication_date END,
                            r.invoice_date
                        ) + rp.return_max_days THEN 'within_window'
                        ELSE 'expired'
                    END AS window_status
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                JOIN stock s ON s.product_id = pp.id
                LEFT JOIN receipts r ON r.product_id = pp.id
                LEFT JOIN LATERAL (
                    SELECT ps.partner_id
                    FROM product_supplierinfo ps
                    WHERE ps.product_tmpl_id = pp.product_tmpl_id
                    AND (ps.date_end IS NULL OR ps.date_end >= CURRENT_DATE)
                    AND (ps.date_start IS NULL OR ps.date_start <= CURRENT_DATE)
                    ORDER BY ps.sequence, ps.id
                    LIMIT 1
                ) vendor ON TRUE
                LEFT JOIN res_partner rp ON rp.id = vendor.partner_id
                WHERE vendor.partner_id IS NOT NULL
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
            # Find existing draft order for this vendor or create one
            order = ReturnOrder.search([
                ('partner_id', '=', vendor.id),
                ('state', '=', 'draft'),
            ], limit=1)
            if not order:
                order = ReturnOrder.create({'partner_id': vendor.id})
            for line in vendor_lines:
                # Skip if product already on the order
                existing = order.order_line.filtered(
                    lambda l: l.product_id.id == line.product_id.id
                )
                if not existing:
                    self.env['vendor.return.order.line'].create({
                        'order_id': order.id,
                        'product_id': line.product_id.id,
                        'product_qty': line.on_hand_qty,
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
