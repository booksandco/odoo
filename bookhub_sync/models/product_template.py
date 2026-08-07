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
