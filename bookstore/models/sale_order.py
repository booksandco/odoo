from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _confirmation_error_message(self):
        error = super()._confirmation_error_message()
        if error:
            return error
        if not self.carrier_id and not self.is_all_service:
            return _("Please select a shipping method before confirming this order.")
        return False

    def _get_real_points_for_coupon(self, coupon, post_confirm=False):
        """Clamp usable coupon points at zero.

        Refunds deduct loyalty points and can leave a card balance negative
        (e.g. store credit already redeemed before the return). Stock Odoo
        then refuses to confirm *any* order that merely earns points on that
        card ("One or more rewards on the sale order is invalid"), because
        action_confirm checks every attached coupon for real points < 0.
        Treat a negative balance as zero usable points instead: earning
        orders confirm normally, and redeeming stays impossible until the
        balance has earned its way back to positive.
        """
        return max(0.0, super()._get_real_points_for_coupon(coupon, post_confirm=post_confirm))
