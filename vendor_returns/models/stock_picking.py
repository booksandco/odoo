from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        res = super().button_validate()
        done_pickings = self.filtered(lambda p: p.state == 'done')
        if done_pickings:
            done_pickings._update_vendor_return_orders()
        return res

    def _update_vendor_return_orders(self):
        order_lines = self.env['vendor.return.order.line'].search([
            ('move_ids.picking_id', 'in', self.ids),
        ])
        for order in order_lines.order_id:
            if order.state == 'confirmed' and order.picking_ids and all(
                p.state in ('done', 'cancel') for p in order.picking_ids
            ):
                if any(p.state == 'done' for p in order.picking_ids):
                    order.state = 'done'
                    order._remove_replenishment_rules()
