from odoo import fields, models


class VendorReturnPolicy(models.Model):
    _name = 'vendor.return.policy'
    _description = 'Vendor Return Policy'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, ondelete='cascade')
    min_days = fields.Integer('Minimum Days', required=True, default=90,
        help='Minimum days before returns are accepted')
    max_days = fields.Integer('Maximum Days', required=True, default=365,
        help='Maximum days after which returns are no longer accepted')
    date_basis = fields.Selection([
        ('invoice', 'Invoice Date'),
        ('publication', 'Publication Date'),
    ], string='Date Basis', default='invoice', required=True)
