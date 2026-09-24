from odoo import api, fields, models


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    x_total_sales = fields.Float(
        compute='_compute_x_total_sales',
        string='Total Sold',
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

    @api.model
    def _get_orderpoint_values(self, product, location):
        """Default new auto-created rules to min=1/max=1 like the rest of the shop.

        Core uses this when the Replenishment view creates a rule for a product
        with a negative forecast; without this override those rules are created
        with min=0/max=0 and never fire on a plain sell-out.
        """
        values = super()._get_orderpoint_values(product, location)
        values.update({'product_min_qty': 1.0, 'product_max_qty': 1.0})
        return values
