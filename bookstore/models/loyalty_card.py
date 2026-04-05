from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class LoyaltyCard(models.Model):
    _inherit = 'loyalty.card'

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for card in res:
            if not card.expiration_date and card.program_id.card_validity_months:
                card.expiration_date = fields.Date.today() + relativedelta(months=card.program_id.card_validity_months)
        return res
