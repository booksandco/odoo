# -*- coding: utf-8 -*-
from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def get_pos_invoice_line_data(self):
        """Return the product lines of this invoice in a format usable by POS.

        Each element is a dict with:
            - product_id
            - product_tmpl_id
            - name (the invoice line description)
            - quantity (converted to the product's default UoM)
            - sequence
        """
        self.ensure_one()
        result = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            if not line.product_id:
                continue
            quantity = line.product_uom_id._compute_quantity(
                line.quantity,
                line.product_id.uom_id,
                round=False,
            )
            result.append({
                'product_id': line.product_id.id,
                'product_tmpl_id': line.product_id.product_tmpl_id.id,
                'name': line.name,
                'quantity': quantity,
                'sequence': line.sequence,
            })
        return result
