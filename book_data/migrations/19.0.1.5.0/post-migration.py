from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Author = env['bookstore.author']
    Publisher = env['bookstore.publisher']

    # Build a cache of publisher records keyed by normalized name.
    publisher_by_name = {}
    cr.execute(
        """
        SELECT DISTINCT x_publisher
        FROM product_template
        WHERE x_publisher IS NOT NULL AND x_publisher != ''
        """
    )
    for (publisher_name,) in cr.fetchall():
        name = publisher_name.strip()
        if not name:
            continue
        norm = name.lower()
        if norm in publisher_by_name:
            continue
        existing = Publisher.search([('name', 'ilike', name)], limit=1)
        if not existing:
            existing = Publisher.create({'name': name})
        publisher_by_name[norm] = existing

    # Build a cache of author records keyed by normalized name.
    author_by_name = {}
    cr.execute(
        """
        SELECT x_author
        FROM product_template
        WHERE x_author IS NOT NULL AND x_author != ''
        """
    )
    for (authors_str,) in cr.fetchall():
        for part in authors_str.split(','):
            name = part.strip()
            if not name:
                continue
            norm = name.lower()
            if norm in author_by_name:
                continue
            existing = Author.search([('name', 'ilike', name)], limit=1)
            if not existing:
                existing = Author.create({'name': name})
            author_by_name[norm] = existing

    # Link publishers directly with SQL to avoid loading all products.
    cr.execute(
        """
        SELECT id, x_publisher
        FROM product_template
        WHERE x_publisher IS NOT NULL AND x_publisher != ''
        """
    )
    for product_id, publisher_name in cr.fetchall():
        name = publisher_name.strip()
        if not name:
            continue
        publisher = publisher_by_name.get(name.lower())
        if publisher:
            cr.execute(
                "UPDATE product_template SET x_publisher_id = %s WHERE id = %s",
                (publisher.id, product_id),
            )

    # Insert author links in batches with SQL, avoiding duplicates.
    cr.execute("SELECT product_template_id, author_id FROM bookstore_product_author")
    seen_product_authors = {tuple(row) for row in cr.fetchall()}

    cr.execute(
        """
        SELECT id, x_author
        FROM product_template
        WHERE x_author IS NOT NULL AND x_author != ''
        ORDER BY id
        """
    )

    batch_size = 5000
    batch = []
    row_count = 0
    for product_id, authors_str in cr.fetchall():
        sequence = 0
        seen_norms = set()
        for part in authors_str.split(','):
            name = part.strip()
            if not name:
                continue
            norm = name.lower()
            if norm in seen_norms:
                continue
            seen_norms.add(norm)
            author = author_by_name.get(norm)
            if author and (product_id, author.id) not in seen_product_authors:
                seen_product_authors.add((product_id, author.id))
                batch.append((product_id, author.id, sequence))
                sequence += 1
                row_count += 1
                if len(batch) >= batch_size:
                    _insert_product_author(cr, batch)
                    cr.commit()
                    batch = []

    if batch:
        _insert_product_author(cr, batch)
        cr.commit()

    # Recompute the denormalised Char fields from the new relations in batches.
    cr.execute(
        """
        SELECT id
        FROM product_template
        WHERE x_publisher_id IS NOT NULL
           OR id IN (SELECT product_template_id FROM bookstore_product_author)
        ORDER BY id
        """
    )
    product_ids = [row[0] for row in cr.fetchall()]

    for offset in range(0, len(product_ids), 1000):
        products = env['product.template'].browse(product_ids[offset:offset + 1000])
        products._sync_author_publisher_chars()
        cr.commit()


def _insert_product_author(cr, rows):
    """Insert bookstore_product_author rows directly with SQL."""
    if not rows:
        return
    args = []
    for product_id, author_id, sequence in rows:
        args.extend([product_id, author_id, sequence])
    placeholders = ','.join('(%s, %s, %s)' for _ in rows)
    cr.execute(
        f"""
        INSERT INTO bookstore_product_author
            (product_template_id, author_id, sequence)
        VALUES {placeholders}
        """,
        args,
    )
