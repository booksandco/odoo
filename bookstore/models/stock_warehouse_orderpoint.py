from odoo import api, fields, models


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    x_total_sales = fields.Float(
        compute='_compute_x_total_sales',
        string='Total Sales',
        digits='Product Unit',
    )

    @api.depends('product_id')
    def _compute_x_total_sales(self):
        self.x_total_sales = 0.0
        if not self:
            return
        done_states = self.env['sale.report'].sudo()._get_done_states()
        product_ids = self.product_id.ids
        domain = [
            ('state', 'in', done_states),
            ('product_id', 'in', product_ids),
        ]
        grouped = self.env['sale.report'].sudo()._read_group(
            domain,
            ['product_id'],
            ['product_uom_qty:sum'],
        )
        qty_map = {product.id: qty for product, qty in grouped}
        for op in self:
            op.x_total_sales = qty_map.get(op.product_id.id, 0.0)
