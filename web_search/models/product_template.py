from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def _search_get_detail(self, website, order, options):
        result = super()._search_get_detail(website, order, options)
        result['search_fields'].extend(['x_author', 'x_publisher'])
        result['fetch_fields'].extend(['x_author', 'x_publisher'])
        result['mapping']['subtitle'] = {'name': 'subtitle_text', 'type': 'text'}
        return result

    def _search_render_results(self, fetch_fields, mapping, icon, limit):
        results_data = super()._search_render_results(fetch_fields, mapping, icon, limit)
        for product, data in zip(self, results_data):
            # Create subtitle with author and publisher
            subtitle_parts = []
            if product.x_author:
                subtitle_parts.append(product.x_author)
            if product.x_publisher:
                subtitle_parts.append(product.x_publisher)
            if subtitle_parts:
                data['subtitle_text'] = ' • '.join(subtitle_parts)
        return results_data
