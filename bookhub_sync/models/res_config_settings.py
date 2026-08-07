from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    bookhub_base_url = fields.Char(
        string='CirclePOS Base URL',
        config_parameter='bookhub_sync.base_url',
        default='https://bco.circlepos.com',
    )
    bookhub_site_domain = fields.Char(
        string='Site Domain',
        config_parameter='bookhub_sync.site_domain',
        help='Sent as the x-site-domain header, e.g. bco.circlepos.com',
    )
    bookhub_client_id = fields.Char(
        string='OAuth Client ID',
        config_parameter='bookhub_sync.client_id',
    )
    bookhub_client_secret = fields.Char(
        string='OAuth Client Secret',
        config_parameter='bookhub_sync.client_secret',
    )

    def action_bookhub_view_queue(self):
        return self.env['bookhub.sync.queue'].action_view_queue()

    def action_bookhub_full_sync(self):
        count = self.env['bookhub.sync.queue'].enqueue_full_sync()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'BookHub Sync',
                'message': f'{count} products queued for sync.',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_bookhub_cleanup(self):
        count = self.env['bookhub.sync.queue'].enqueue_cleanup()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'BookHub Sync',
                'message': f'{count} Circle products queued to be hidden.',
                'type': 'success',
                'sticky': False,
            },
        }
