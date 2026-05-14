from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Update views that still reference the old model name.
    # Use raw SQL to bypass ORM validation (the new model is not in the
    # registry yet during pre-migration).
    cr.execute("""
        UPDATE ir_ui_view
           SET model = 'bookstore.purchase.suggestion'
         WHERE model = 'customer.order'
    """)

    # Update window actions that still reference the old model name.
    cr.execute("""
        UPDATE ir_act_window
           SET res_model = 'bookstore.purchase.suggestion'
         WHERE res_model = 'customer.order'
    """)

    # Drop the old SQL view so the new model's init() can create its own.
    cr.execute("DROP VIEW IF EXISTS customer_order")

    # Remove access rules tied to the old model.
    cr.execute("""
        DELETE FROM ir_model_access
         WHERE model_id IN (SELECT id FROM ir_model WHERE model = 'customer.order')
    """)

    # Remove fields tied to the old model.
    cr.execute("""
        DELETE FROM ir_model_fields
         WHERE model_id IN (SELECT id FROM ir_model WHERE model = 'customer.order')
    """)

    # Remove the old ir.model record itself.
    cr.execute("DELETE FROM ir_model WHERE model = 'customer.order'")

    # Clean up the auto-generated xmlid for the old model so that
    # _process_end does not try to unlink a missing record.
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model'
           AND name = 'model_customer_order'
           AND module = 'customer_to_order'
    """)
