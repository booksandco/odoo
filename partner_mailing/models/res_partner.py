from odoo import api, models, tools


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        self._partner_mailing_auto_subscribe(partners)
        return partners

    def _partner_mailing_auto_subscribe(self, partners):
        list_id = int(
            self.env['ir.config_parameter']
            .sudo()
            .get_param('partner_mailing.default_list_id', '0')
        )
        if not list_id:
            return

        mailing_list = self.env['mailing.list'].sudo().browse(list_id).exists()
        if not mailing_list or not mailing_list.active:
            return

        MailingContact = self.env['mailing.contact'].sudo()
        Subscription = self.env['mailing.subscription'].sudo()

        for partner in partners:
            email_normalized = tools.email_normalize(partner.email or '')
            if not email_normalized:
                continue

            # Check if a subscription already exists for this email on the list
            existing_sub = Subscription.search([
                ('list_id', '=', list_id),
                ('contact_id.email_normalized', '=', email_normalized),
            ], limit=1)
            if existing_sub:
                continue

            # Find or create the mailing contact
            contact = MailingContact.search([
                ('email_normalized', '=', email_normalized),
            ], limit=1)
            if not contact:
                contact = MailingContact.create({
                    'name': partner.name or email_normalized,
                    'email': partner.email,
                })

            Subscription.create({
                'contact_id': contact.id,
                'list_id': list_id,
            })
