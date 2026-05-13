from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    x_isbn = fields.Char(
        string="ISBN",
        related='product_id.barcode',
        readonly=True,
    )
