from odoo import fields, models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    card_validity_months = fields.Integer(
        string="Card Validity (Months)",
        help="Number of months after creation before a gift card expires. Leave at 0 for no expiry.",
    )
