from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _confirmation_error_message(self):
        error = super()._confirmation_error_message()
        if error:
            return error
        if not self.carrier_id:
            return _("Please select a shipping method before confirming this order.")
        return False
