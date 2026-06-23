from odoo import fields, models


class BookProductAuthor(models.Model):
    _name = 'bookstore.product_author'
    _description = 'Product Author Link'
    _order = 'sequence, id'

    product_template_id = fields.Many2one(
        'product.template',
        string='Product',
        required=True,
        ondelete='cascade',
        index=True,
    )
    author_id = fields.Many2one(
        'bookstore.author',
        string='Author',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Sequence', default=0)

    _product_author_uniq = models.Constraint(
        'unique(product_template_id, author_id)',
        'An author can only be linked once per product.',
    )
