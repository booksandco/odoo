from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    hardcover_api_key = fields.Char(
        string="Hardcover API Key",
        config_parameter='book_data.hardcover_api_key',
    )
    titlepage_api_token = fields.Char(
        string="Titlepage API Token",
        config_parameter='book_data.titlepage_api_token',
    )
