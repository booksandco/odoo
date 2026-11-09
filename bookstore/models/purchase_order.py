from odoo import models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def action_view_picking(self):
        open_receipts = self.picking_ids.filtered(
            lambda picking: picking.picking_type_code == 'incoming'
            and picking.state not in ('done', 'cancel')
        )
        if open_receipts:
            return self._get_action_view_picking(open_receipts)
        return super().action_view_picking()
