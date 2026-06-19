from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ProductTemplate = env['product.template']
    Author = env['bookstore.author']
    Publisher = env['bookstore.publisher']
    ProductAuthor = env['bookstore.product_author']

    # Build a cache of publisher records keyed by normalized name.
    publisher_by_name = {}
    env.cr.execute(
        """
        SELECT id, x_publisher
        FROM product_template
        WHERE x_publisher IS NOT NULL AND x_publisher != ''
        """
    )
    publisher_names = {row[1].strip() for row in env.cr.fetchall() if row[1].strip()}
    for name in publisher_names:
        norm = name.lower()
        if norm in publisher_by_name:
            continue
        existing = Publisher.search([('name', 'ilike', name)], limit=1)
        if not existing:
            existing = Publisher.create({'name': name})
        publisher_by_name[norm] = existing

    # Build a cache of author records keyed by normalized name.
    author_by_name = {}
    env.cr.execute(
        """
        SELECT id, x_author
        FROM product_template
        WHERE x_author IS NOT NULL AND x_author != ''
        """
    )
    author_names = set()
    for row in env.cr.fetchall():
        for part in row[1].split(','):
            name = part.strip()
            if name:
                author_names.add(name)

    for name in author_names:
        norm = name.lower()
        if norm in author_by_name:
            continue
        existing = Author.search([('name', 'ilike', name)], limit=1)
        if not existing:
            existing = Author.create({'name': name})
        author_by_name[norm] = existing

    # Link publishers and authors to products.
    env.cr.execute(
        """
        SELECT id, x_author, x_publisher
        FROM product_template
        WHERE (x_author IS NOT NULL AND x_author != '')
           OR (x_publisher IS NOT NULL AND x_publisher != '')
        """
    )
    product_author_values = []
    for product_id, authors_str, publisher_name in env.cr.fetchall():
        if publisher_name:
            publisher = publisher_by_name.get(publisher_name.strip().lower())
            if publisher:
                env.cr.execute(
                    """
                    UPDATE product_template
                    SET x_publisher_id = %s
                    WHERE id = %s
                    """,
                    (publisher.id, product_id),
                )
        if authors_str:
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
                if author:
                    product_author_values.append({
                        'product_template_id': product_id,
                        'author_id': author.id,
                        'sequence': sequence,
                    })
                    sequence += 1

    if product_author_values:
        ProductAuthor.create(product_author_values)

    # Recompute the denormalised Char fields from the new relations.
    env.cr.execute("SELECT id FROM product_template WHERE x_publisher_id IS NOT NULL OR id IN (SELECT product_template_id FROM bookstore_product_author)")
    product_ids = [row[0] for row in env.cr.fetchall()]
    for batch in range(0, len(product_ids), 1000):
        products = ProductTemplate.browse(product_ids[batch:batch + 1000])
        products._sync_author_publisher_chars()
