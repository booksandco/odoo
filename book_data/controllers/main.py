from odoo import http
from odoo.fields import Domain
from odoo.http import request

from odoo.addons.website_sale.controllers.main import SHOP_PATH, WebsiteSale


class BookDataWebsiteSale(WebsiteSale):

    @http.route([
        '/shop/author/<model("bookstore.author"):author>',
        '/shop/author/<model("bookstore.author"):author>/page/<int:page>',
    ], type='http', auth='public', website=True, sitemap=False)
    def shop_by_author(self, author, page=0, search='', **post):
        if not request.website.has_ecommerce_access():
            return request.redirect(f'/web/login?redirect={request.httprequest.path}')
        return self._render_entity_listing('author', author, page=page, search=search, **post)

    @http.route([
        '/shop/publisher/<model("bookstore.publisher"):publisher>',
        '/shop/publisher/<model("bookstore.publisher"):publisher>/page/<int:page>',
    ], type='http', auth='public', website=True, sitemap=False)
    def shop_by_publisher(self, publisher, page=0, search='', **post):
        if not request.website.has_ecommerce_access():
            return request.redirect(f'/web/login?redirect={request.httprequest.path}')
        return self._render_entity_listing('publisher', publisher, page=page, search=search, **post)

    def _render_entity_listing(self, entity_type, entity, page=0, search='', **post):
        website = request.env['website'].get_current_website()
        domain = Domain(request.website.sale_product_domain())

        if entity_type == 'author':
            domain &= Domain('author_line_ids.author_id', '=', entity.id)
            title = f'Books by {entity.name}'
        else:
            domain &= Domain('x_publisher_id', '=', entity.id)
            title = f'Books from {entity.name}'

        if search:
            for term in search.split():
                term_domain = Domain.OR([
                    Domain('name', 'ilike', term),
                    Domain('x_author', 'ilike', term),
                    Domain('x_publisher', 'ilike', term),
                ])
                domain &= term_domain

        order = post.get('order') or website.shop_default_sort or 'website_sequence asc'
        ProductTemplate = request.env['product.template'].with_context(bin_size=True)
        product_count = ProductTemplate.search_count(domain)

        ppg = website.shop_ppg or 21
        ppr = website.shop_ppr or 4
        gap = website.shop_gap or '16px'

        url_base = f'/shop/{entity_type}/{request.env["ir.http"]._slug(entity)}'
        url_args = {}
        if search:
            url_args['search'] = search
        if post.get('order'):
            url_args['order'] = post['order']

        pager = website.pager(
            url=url_base,
            total=product_count,
            page=page,
            step=ppg,
            scope=5,
            url_args=url_args,
        )
        offset = pager['offset']
        products = ProductTemplate.search(domain, order=order, limit=ppg, offset=offset)
        products.fetch()

        variants = request.env['product.product'].sudo().browse(
            product._get_first_possible_variant_id() for product in products
        )
        variants.fetch()
        product_variants = dict(zip(products, variants))
        products_prices = products._get_sales_prices(website)

        values = {
            'website': website,
            'entity': entity,
            'entity_type': entity_type,
            'title': title,
            'products': products,
            'product_variants': product_variants,
            'pager': pager,
            'search': search,
            'order': post.get('order', ''),
            'ppg': ppg,
            'ppr': ppr,
            'gap': gap,
            'get_product_prices': lambda product: products_prices[product.id],
            'shop_path': SHOP_PATH,
        }
        return request.render('book_data.shop_listing', values)

    def _prepare_product_values(self, product, category, **kwargs):
        values = super()._prepare_product_values(product, category, **kwargs)
        if product.x_hardcover_book_id:
            domain = Domain(request.website.sale_product_domain()) & Domain([
                ('x_hardcover_book_id', '=', product.x_hardcover_book_id),
                ('id', '!=', product.id),
            ])
            other_editions = request.env['product.template'].with_context(
                bin_size=True
            ).search(domain)
        else:
            other_editions = request.env['product.template']
        values['other_editions'] = other_editions
        return values
