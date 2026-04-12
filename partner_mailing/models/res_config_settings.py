from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    partner_mailing_list_id = fields.Many2one(
        'mailing.list',
        string='Auto-Subscribe Mailing List',
        config_parameter='partner_mailing.default_list_id',
        help='Newly created contacts with an email address will be '
             'automatically added to this mailing list.',
    )
