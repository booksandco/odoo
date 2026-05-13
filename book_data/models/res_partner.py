from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_titlepage_name = fields.Char(
        string="Titlepage Supplier Name",
        help="Exact supplier name used by Titlepage for automatic vendor matching.",
    )
