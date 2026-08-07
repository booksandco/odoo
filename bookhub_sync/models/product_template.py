from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    bookhub_synced = fields.Boolean(
        string='Synced to BookHub',
        default=False,
        copy=False,
        readonly=True,
        help='Set once this product has been pushed to the CirclePOS shadow '
             'site. Used to keep syncing products whose stock drops to zero.',
    )
    bookhub_last_stock = fields.Integer(
        string='Last Stock Sent to BookHub',
        default=-1,
        copy=False,
        readonly=True,
        help='Stock level at the last enqueue. -1 means unknown. Used to '
             'detect zero-crossings, which require a product import (to '
             'flip the hidden flag) rather than a bare stock update.',
    )
