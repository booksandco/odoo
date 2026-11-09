from odoo import api, fields, models


class BookstoreReceiveAllWarning(models.TransientModel):
    _name = 'bookstore.receive.all.warning'
    _description = 'Confirm receiving all purchase order items'

    picking_ids = fields.Many2many('stock.picking', string='Receipts', required=True)
    picking_names = fields.Char(compute='_compute_summary')
    po_names = fields.Char(compute='_compute_summary')
    line_count = fields.Integer(compute='_compute_summary')
    unit_count = fields.Float(compute='_compute_summary')

    @api.depends('picking_ids')
    def _compute_summary(self):
        for wizard in self:
            moves = wizard.picking_ids.move_ids.filtered(
                lambda move: move.state not in ('done', 'cancel')
            )
            wizard.picking_names = ', '.join(sorted(wizard.picking_ids.mapped('name')))
            wizard.po_names = ', '.join(
                sorted(set(moves.purchase_line_id.order_id.mapped('name')))
            )
            wizard.line_count = len(moves)
            wizard.unit_count = sum(moves.mapped('quantity'))

    def action_receive_all(self):
        self.ensure_one()
        return self.picking_ids.with_context(
            bookstore_receive_all_confirmed=True
        ).button_validate()
