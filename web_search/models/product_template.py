from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def _search_get_detail(self, website, order, options):
        result = super()._search_get_detail(website, order, options)
        result['search_fields'].extend(['x_author', 'x_publisher'])
        result['fetch_fields'].extend(['x_author', 'x_publisher'])
        return result
