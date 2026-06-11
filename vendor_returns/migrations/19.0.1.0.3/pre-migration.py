def migrate(cr, version):
    # Backfill any existing vendor return order lines that were created before
    # product_uom became required. Use the product template's UoM so the
    # subsequent NOT NULL constraint can be applied safely.
    cr.execute("""
        UPDATE vendor_return_order_line vrol
           SET product_uom = pt.uom_id
          FROM product_product pp
          JOIN product_template pt ON pt.id = pp.product_tmpl_id
         WHERE vrol.product_uom IS NULL
           AND vrol.product_id = pp.id
    """)

    # Guard against edge-case rows that still have no UoM (e.g. missing product).
    cr.execute("""
        SELECT COUNT(*) FROM vendor_return_order_line WHERE product_uom IS NULL
    """)
    if cr.fetchone()[0]:
        raise ValueError(
            "Cannot enforce NOT NULL on vendor_return_order_line.product_uom: "
            "some rows still have no product_uom and no product to derive it from."
        )

    cr.execute("""
        ALTER TABLE vendor_return_order_line
        ALTER COLUMN product_uom SET NOT NULL
    """)
