from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    return_min_days = fields.Integer('Return Min Days', default=0,
        help='Minimum days before returns are accepted')
    return_max_days = fields.Integer('Return Max Days', default=0,
        help='Maximum days after which returns are no longer accepted')
    return_date_basis = fields.Selection([
        ('invoice', 'Invoice Date'),
        ('publication', 'Publication Date'),
    ], string='Return Date Basis', default='invoice')
