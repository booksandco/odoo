from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_author = fields.Char(string="Author")
    x_publisher = fields.Char(string="Publisher")

    author_line_ids = fields.One2many(
        'bookstore.product_author',
        'product_template_id',
        string='Author Links',
    )
    author_ids = fields.Many2many(
        'bookstore.author',
        string='Authors',
        compute='_compute_author_ids',
        inverse='_inverse_author_ids',
        store=False,
    )
    x_publisher_id = fields.Many2one(
        'bookstore.publisher',
        string='Publisher Record',
    )

    @api.onchange('x_publisher_id')
    def _onchange_x_publisher_id(self):
        for template in self:
            template.x_publisher = template.x_publisher_id.name or False

    x_hardcover_book_id = fields.Integer(string='Hardcover Book ID')
    x_hardcover_edition_id = fields.Integer(string='Hardcover Edition ID')
    x_hardcover_editions_json = fields.Text(string='Hardcover Editions JSON')

    x_publication_date = fields.Date(string="Publication Date")
    x_last_sale_date = fields.Date(
        string="Last Sale",
        compute='_compute_x_last_sale_date',
        store=True,
    )
    x_last_vendor = fields.Many2one(
        'res.partner',
        string="Last Vendor",
        related='seller_ids.partner_id',
        readonly=True,
    )
    x_is_isbn = fields.Boolean(compute='_compute_is_isbn')

    @api.depends('barcode')
    def _compute_is_isbn(self):
        for rec in self:
            rec.x_is_isbn = bool(rec.barcode and rec.barcode.startswith(('978', '979')))

    @api.depends('author_line_ids.author_id')
    def _compute_author_ids(self):
        for template in self:
            template.author_ids = template.author_line_ids.author_id

    def _inverse_author_ids(self):
        for template in self:
            desired = template.author_ids
            lines = [fields.Command.clear()]
            for idx, author in enumerate(desired):
                lines.append(fields.Command.create({
                    'author_id': author.id,
                    'sequence': idx,
                }))
            template.author_line_ids = lines
            template.x_author = ', '.join(a.name for a in desired if a.name) or False

    def _sync_author_publisher_chars(self):
        """Keep the legacy Char fields in sync with the relation fields."""
        for template in self:
            author_names = [
                line.author_id.name
                for line in template.author_line_ids.sorted('sequence')
                if line.author_id.name
            ]
            template.x_author = ', '.join(author_names) if author_names else False
            template.x_publisher = template.x_publisher_id.name or False

    @api.depends('product_variant_ids.stock_move_ids')
    def _compute_x_last_sale_date(self):
        for template in self:
            if not template.product_variant_id:
                template.x_last_sale_date = False
                continue
            moves = self.env['stock.move'].search([
                ('product_id', '=', template.product_variant_id.id),
                ('picking_type_id.code', '=', 'outgoing'),
                ('state', '!=', 'cancel'),
            ], order='date desc', limit=1)
            template.x_last_sale_date = moves.date if moves else False

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
