#!/usr/bin/env python3
"""Backfill Hardcover metadata + reviews for all published ISBN products.

Run inside an odoo-bin shell, e.g.:

    odoo-bin shell -c /path/to/odoo.conf -d mydb < book_data/scripts/refresh_published_hardcover.py

The script iterates over every published product with an ISBN barcode, calls
action_refresh_hardcover_data(), commits after each product, and logs failures
per-product without stopping the batch.
"""
import logging
import time
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

# Seconds to sleep between API calls to stay polite to Hardcover.
SLEEP_BETWEEN_CALLS = 0.5

# Skip products refreshed in the last N hours (allows safe resumption).
SKIP_RECENTLY_REFRESHED_HOURS = 1

Product = env['product.template']
cutoff = datetime.utcnow() - timedelta(hours=SKIP_RECENTLY_REFRESHED_HOURS)

products = Product.search([
    ('is_published', '=', True),
    '|',
    ('barcode', '=like', '978%'),
    ('barcode', '=like', '979%'),
    '|',
    ('x_hardcover_reviews_fetch_date', '=', False),
    ('x_hardcover_reviews_fetch_date', '<', cutoff),
])

total = len(products)
_logger.info("Found %s published ISBN product(s) to refresh", total)

success = 0
failed = 0
skipped = 0
for idx, product in enumerate(products, start=1):
    if (
        product.x_hardcover_reviews_fetch_date
        and product.x_hardcover_reviews_fetch_date >= cutoff
    ):
        skipped += 1
        continue

    try:
        _logger.info(
            "[%s/%s] Refreshing Hardcover data for %s (ISBN: %s)",
            idx, total, product.name, product.barcode,
        )
        product.action_refresh_hardcover_data()
        env.cr.commit()
        success += 1
    except Exception as e:
        env.cr.rollback()
        failed += 1
        _logger.warning(
            "[%s/%s] Failed to refresh %s (ISBN: %s): %s",
            idx, total, product.name, product.barcode, e,
        )
    if idx < total:
        time.sleep(SLEEP_BETWEEN_CALLS)

_logger.info(
    "Backfill complete: %s succeeded, %s failed, %s skipped out of %s total.",
    success, failed, skipped, total,
)
print(
    f"Backfill complete: {success} succeeded, {failed} failed, "
    f"{skipped} skipped out of {total} total."
)
