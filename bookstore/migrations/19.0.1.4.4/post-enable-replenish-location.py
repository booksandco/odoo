import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Flag each warehouse's stock location as a replenishment location.

    The field post-dates this database's warehouses, so they never got the
    default. Without it the Replenishment view cannot auto-create reordering
    rules for products whose forecast goes negative, so sold-out items rely
    entirely on the incoming-picking automation. Enabling it makes core create
    min=1/max=1 rules (see _get_orderpoint_values) for books and giftware alike.
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    updated = env['stock.location']
    for warehouse in env['stock.warehouse'].search([]):
        stock_location = warehouse.lot_stock_id
        if stock_location and not stock_location.replenish_location:
            stock_location.replenish_location = True
            updated |= stock_location
    if updated:
        _logger.info(
            "Replenishment: enabled replenish_location on %s stock location(s): %s",
            len(updated),
            ", ".join(updated.mapped('display_name')),
        )
