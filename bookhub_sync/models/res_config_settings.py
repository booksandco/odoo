from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    bookhub_webhook_url = fields.Char(
        string='BookHub Webhook URL',
        help='Endpoint that receives product/stock update notifications.',
        config_parameter='bookhub_sync.webhook_url',
    )
