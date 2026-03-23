from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_is_isbn = fields.Boolean(compute='_compute_is_isbn')

    @api.depends('barcode')
    def _compute_is_isbn(self):
        for rec in self:
            rec.x_is_isbn = bool(rec.barcode and rec.barcode.startswith(('978', '979')))

    @api.onchange('categ_id')
    def _onchange_categ_id_sync_public_category(self):
        """When product category changes, find the matching website category by name
        and swap it in, preserving any other public categories."""
        if not self.categ_id:
            return
        PublicCategory = self.env['product.public.category']
        new_public_categ = PublicCategory.search(
            [('name', '=', self.categ_id.name)], limit=1,
        )
        if not new_public_categ:
            return
        old_categ_name = self._origin.categ_id.name if self._origin.categ_id else False
        self.public_categ_ids = (
            self.public_categ_ids.filtered(lambda c: c.name != old_categ_name) | new_public_categ
        )
