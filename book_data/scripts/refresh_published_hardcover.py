#!/usr/bin/env python3
"""Backfill Hardcover metadata + reviews for all published ISBN products.

Run inside an odoo-bin shell, e.g.:

    odoo-bin shell -c /path/to/odoo.conf -d mydb < book_data/scripts/refresh_published_hardcover.py

The script iterates over every published product with an ISBN barcode, calls
action_refresh_hardcover_data(), and commits at the end. Failures are logged
per-product without stopping the batch.
"""
import logging
import time

_logger = logging.getLogger(__name__)

# Seconds to sleep between API calls to stay polite to Hardcover.
SLEEP_BETWEEN_CALLS = 0.5

products = env['product.template'].search([
    ('is_published', '=', True),
    ('x_is_isbn', '=', True),
])

total = len(products)
_logger.info("Found %s published ISBN product(s) to refresh", total)

success = 0
failed = 0
for idx, product in enumerate(products, start=1):
    try:
        _logger.info(
            "[%s/%s] Refreshing Hardcover data for %s (ISBN: %s)",
            idx, total, product.name, product.barcode,
        )
        product.action_refresh_hardcover_data()
        success += 1
    except Exception as e:
        failed += 1
        _logger.warning(
            "[%s/%s] Failed to refresh %s (ISBN: %s): %s",
            idx, total, product.name, product.barcode, e,
        )
    if idx < total:
        time.sleep(SLEEP_BETWEEN_CALLS)

env.cr.commit()
_logger.info(
    "Backfill complete: %s succeeded, %s failed out of %s total.",
    success, failed, total,
)
print(f"Backfill complete: {success} succeeded, {failed} failed out of {total} total.")
