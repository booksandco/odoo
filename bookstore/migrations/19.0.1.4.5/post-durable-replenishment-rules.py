import logging
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Give in-stock / previously-sold products a durable reordering rule.

    Rules created programmatically are owned by __system__ (SUPERUSER), and
    core's ``_unlink_processed_orderpoints`` hard-deletes manual SUPERUSER rules
    with ``qty_to_order <= 0`` every time the Replenishment view is opened. That
    is exactly the state a product is in while it is in stock, so the 19.0.1.4.3
    backfill wiped itself out for anything on the shelf.

    Here we (re)create rules owned by the store admin instead, which core leaves
    alone. Scope: active, storable products that hold stock or have ever sold,
    and that do not already have a durable active rule. Products whose only rule
    is archived are left alone (that is a deliberate "never reorder").
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    owner = env.ref('base.user_admin', raise_if_not_found=False)
    if not owner or not owner.active:
        _logger.warning(
            "Durable replenishment rules: no active base.user_admin, skipping."
        )
        return

    cr.execute("""
        SELECT DISTINCT sq.product_id
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN product_product pp ON pp.id = sq.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE sl.usage = 'internal' AND sq.quantity > 0
          AND pp.active AND pt.active AND pt.is_storable
    """)
    target_ids = {row[0] for row in cr.fetchall()}

    cr.execute("""
        SELECT DISTINCT sm.product_id
        FROM stock_move sm
        JOIN stock_location ls ON ls.id = sm.location_id
        JOIN stock_location ld ON ld.id = sm.location_dest_id
        JOIN product_product pp ON pp.id = sm.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE sm.state = 'done'
          AND ls.usage = 'internal' AND ld.usage = 'customer'
          AND pp.active AND pt.active AND pt.is_storable
    """)
    target_ids |= {row[0] for row in cr.fetchall()}
    if not target_ids:
        return

    orderpoint = env['stock.warehouse.orderpoint'].with_context(active_test=False)
    existing = orderpoint.search([('product_id', 'in', list(target_ids))])
    by_product = defaultdict(list)
    for op in existing:
        by_product[op.product_id.id].append(op)

    durable_product_ids = {
        op.product_id.id
        for op in existing
        if op.active and op.create_uid.id != SUPERUSER_ID
    }

    to_create = []
    to_adopt_ids = []
    for product_id in target_ids:
        if product_id in durable_product_ids:
            continue
        rules = by_product.get(product_id)
        if not rules:
            to_create.append(product_id)
            continue
        active_ids = [op.id for op in rules if op.active]
        if active_ids:
            to_adopt_ids.extend(active_ids)
        # else: only archived rules -> deliberate "never reorder", leave alone

    if to_adopt_ids:
        cr.execute(
            "UPDATE stock_warehouse_orderpoint SET create_uid = %s WHERE id IN %s",
            (owner.id, tuple(to_adopt_ids)),
        )

    if to_create:
        env['stock.warehouse.orderpoint'].with_user(owner).create([{
            'product_id': product_id,
            'trigger': 'manual',
            'product_min_qty': 1.0,
            'product_max_qty': 1.0,
        } for product_id in to_create])

    _logger.info(
        "Durable replenishment rules: adopted %s existing rule(s), created %s new rule(s) for %s target product(s).",
        len(to_adopt_ids),
        len(to_create),
        len(target_ids),
    )
