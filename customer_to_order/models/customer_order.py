from odoo import fields, models, tools, _
from odoo.exceptions import UserError


class CustomerOrder(models.Model):
    _name = 'customer.order'
    _description = 'Customer Order'
    _auto = False
    _order = 'sale_order_id desc, id desc'

    sale_order_id = fields.Many2one('sale.order', string='Sale Order', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', readonly=True)
    seller_id = fields.Many2one('res.partner', string='Vendor', readonly=True)
    qty_ordered = fields.Float(string='Qty Ordered', readonly=True)
    qty_delivered = fields.Float(string='Qty Delivered', readonly=True)
    qty_to_deliver = fields.Float(string='Qty to Deliver', readonly=True)
    status = fields.Selection([
        ('available', 'Available'),
        ('on_order', 'On Order'),
        ('unordered', 'Unordered'),
    ], string='Status', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    sol.id AS id,
                    sol.order_id AS sale_order_id,
                    so.partner_id AS partner_id,
                    sol.product_id AS product_id,
                    pp.product_tmpl_id AS product_tmpl_id,
                    (
                        SELECT ps.partner_id
                        FROM product_supplierinfo ps
                        WHERE ps.product_tmpl_id = pp.product_tmpl_id
                        AND (ps.date_end IS NULL OR ps.date_end >= CURRENT_DATE)
                        AND (ps.date_start IS NULL OR ps.date_start <= CURRENT_DATE)
                        ORDER BY ps.sequence, ps.id
                        LIMIT 1
                    ) AS seller_id,
                    sol.product_uom_qty AS qty_ordered,
                    COALESCE(sol.qty_delivered, 0) AS qty_delivered,
                    (sol.product_uom_qty - COALESCE(sol.qty_delivered, 0)) AS qty_to_deliver,
                    CASE
                        WHEN COALESCE(sq.free_qty, 0) >= (sol.product_uom_qty - COALESCE(sol.qty_delivered, 0))
                            THEN 'available'
                        WHEN EXISTS (
                            SELECT 1
                            FROM purchase_order_line pol
                            JOIN purchase_order po ON po.id = pol.order_id
                            WHERE pol.product_id = sol.product_id
                            AND po.state IN ('draft', 'sent', 'to approve', 'purchase')
                            AND pol.qty_received < pol.product_qty
                        ) THEN 'on_order'
                        ELSE 'unordered'
                    END AS status
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id
                JOIN product_product pp ON pp.id = sol.product_id
                LEFT JOIN (
                    SELECT product_id, SUM(quantity - reserved_quantity) AS free_qty
                    FROM stock_quant
                    WHERE location_id IN (
                        SELECT id FROM stock_location WHERE usage = 'internal'
                    )
                    GROUP BY product_id
                ) sq ON sq.product_id = sol.product_id
                WHERE sol.product_uom_qty > COALESCE(sol.qty_delivered, 0)
                AND sol.product_id IS NOT NULL
                AND so.state = 'sale'
            )
        """ % self._table)

    def action_create_po(self):
        lines = self.filtered(lambda l: l.status == 'unordered')
        if not lines:
            raise UserError(_("Please select lines with 'Unordered' status."))

        vendor_lines = {}
        no_vendor = self.env['product.product']
        for line in lines:
            if not line.seller_id:
                no_vendor |= line.product_id
            else:
                vendor_lines.setdefault(line.seller_id, self.env['customer.order'])
                vendor_lines[line.seller_id] |= line

        if no_vendor:
            raise UserError(_(
                "The following products have no vendor set:\n%s",
                '\n'.join(no_vendor.mapped('display_name'))
            ))

        pos = self.env['purchase.order']
        for vendor, order_lines in vendor_lines.items():
            po = self.env['purchase.order'].search([
                ('partner_id', '=', vendor.id),
                ('state', '=', 'draft'),
                ('x_purchase_type', '=', 'customer_order'),
            ], limit=1)
            if not po:
                po = self.env['purchase.order'].create({
                    'partner_id': vendor.id,
                    'x_purchase_type': 'customer_order',
                })
            for line in order_lines:
                existing_pol = po.order_line.filtered(
                    lambda l, p=line.product_id: l.product_id == p
                )
                if existing_pol:
                    existing_pol[0].product_qty += line.qty_to_deliver
                else:
                    self.env['purchase.order.line'].create({
                        'order_id': po.id,
                        'product_id': line.product_id.id,
                        'product_qty': line.qty_to_deliver,
                    })
            pos |= po

        if len(pos) == 1:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'purchase.order',
                'view_mode': 'form',
                'res_id': pos.id,
            }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pos.ids)],
        }
