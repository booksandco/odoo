from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class LoyaltyCard(models.Model):
    _inherit = 'loyalty.card'

    @api.model_create_multi
    def create(self, vals_list):
        programs = self.env['loyalty.program'].browse(
            {v['program_id'] for v in vals_list if v.get('program_id')}
        )
        validity_by_program = {
            p.id: p.card_validity_months for p in programs if p.card_validity_months
        }
        for vals in vals_list:
            months = validity_by_program.get(vals.get('program_id'))
            if months:
                vals['expiration_date'] = fields.Date.today() + relativedelta(months=months)
        return super().create(vals_list)
