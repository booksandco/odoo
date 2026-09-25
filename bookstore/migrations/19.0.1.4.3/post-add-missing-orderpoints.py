import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Backfill replenishment rules for sold ISBN products that never got one.

    The incoming-picking automation only created a rule when it also changed the
    product's vendor, so titles that were already set up with their distributor
    never received a rule and could not appear in the Replenishment list. Create
    min=1/max=1 manual rules for active, storable, ISBN products that sold in the
    last year and have no rule at all (including archived ones).
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    cutoff = fields.Datetime.now() - timedelta(days=365)
    moves = env['stock.move'].search_read([
        ('state', '=', 'done'),
        ('location_id.usage', '=', 'internal'),
        ('location_dest_id.usage', '=', 'customer'),
        ('date', '>=', cutoff),
    ], ['product_id'])
    sold_product_ids = {move['product_id'][0] for move in moves if move['product_id']}
    if not sold_product_ids:
        return

    products = env['product.product'].search([
        ('id', 'in', list(sold_product_ids)),
        ('active', '=', True),
        ('is_storable', '=', True),
        '|', ('barcode', '=like', '978%'), ('barcode', '=like', '979%'),
    ])
    if not products:
        return

    orderpoint = env['stock.warehouse.orderpoint'].with_context(active_test=False)
    ruled_product_ids = set(
        orderpoint.search([('product_id', 'in', products.ids)]).mapped('product_id').ids
    )
    to_create = products.filtered(lambda product: product.id not in ruled_product_ids)
    if not to_create:
        return

    env['stock.warehouse.orderpoint'].create([{
        'product_id': product.id,
        'trigger': 'manual',
        'product_min_qty': 1.0,
        'product_max_qty': 1.0,
    } for product in to_create])
    _logger.info(
        "Replenishment backfill: created %s rules for sold ISBN products.",
        len(to_create),
    )
