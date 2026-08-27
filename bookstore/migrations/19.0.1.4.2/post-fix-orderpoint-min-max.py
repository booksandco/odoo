from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Reordering rules created by the "Update supplier info on incoming
    # products" automated action (and a few leftovers from the March import)
    # were created with the default product_min_qty=0 / product_max_qty=0.
    # With min=0 a rule only fires when the forecast goes NEGATIVE (customer
    # backorder), so a plain sell-out never appears in the "To Reorder" list.
    # Raise these to min=1 / max=1 to match every other rule in the shop so
    # newly received products start appearing as soon as they sell out.
    orderpoints = env['stock.warehouse.orderpoint'].search([
        ('trigger', '=', 'manual'),
        ('product_min_qty', '=', 0.0),
        ('product_id.active', '=', True),
    ])
    if orderpoints:
        orderpoints.write({'product_min_qty': 1.0, 'product_max_qty': 1.0})
        # qty_to_order_computed is stored; recompute it for the touched rules
        # so the replenishment list reflects the new thresholds immediately.
        env.add_to_compute(
            env['stock.warehouse.orderpoint']._fields['qty_to_order_computed'],
            orderpoints,
        )
