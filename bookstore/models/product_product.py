from odoo import api, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.readonly
    def get_formview_action(self, access_uid=None):
        if not self.env.user.has_group('product.group_product_variant'):
            self.ensure_one()
            tmpl_action = self.product_tmpl_id.get_formview_action(access_uid=access_uid)
            return tmpl_action
        return super().get_formview_action(access_uid=access_uid)
