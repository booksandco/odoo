from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_is_isbn = fields.Boolean(compute='_compute_is_isbn')

    @api.depends('barcode')
    def _compute_is_isbn(self):
        for rec in self:
            rec.x_is_isbn = bool(rec.barcode and rec.barcode.startswith(('978', '979')))
