from odoo import _, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        if self.env.context.get('bookstore_receive_all_confirmed'):
            return super().button_validate()

        full_receipts = self.filtered(lambda picking: picking._is_full_purchase_receipt())
        if full_receipts:
            return full_receipts._action_receive_all_warning()

        return super().button_validate()

    def _is_full_purchase_receipt(self):
        self.ensure_one()
        if self.picking_type_id.code != 'incoming':
            return False
        if self.state in ('draft', 'done', 'cancel') or self.return_id:
            return False

        moves = self.move_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
        if not moves or any(not move.purchase_line_id for move in moves):
            return False
        if any(move.origin_returned_move_id for move in moves):
            return False

        demand_moves = moves.filtered(
            lambda move: move.product_uom.compare(move.product_uom_qty, 0) > 0
        )
        if not demand_moves:
            return False
        if not all(
            move.product_uom.compare(move.quantity, move.product_uom_qty) >= 0
            for move in demand_moves
        ):
            return False

        return not any(move.picked for move in demand_moves)

    def _action_receive_all_warning(self):
        view = self.env.ref('bookstore.view_bookstore_receive_all_warning_form')
        return {
            'name': _('Receive all items?'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'bookstore.receive.all.warning',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': {
                **self.env.context,
                'default_picking_ids': [(6, 0, self.ids)],
            },
        }
