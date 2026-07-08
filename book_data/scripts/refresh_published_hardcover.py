#!/usr/bin/env python3
"""Backfill Titlepage metadata for ISBN products that have no Hardcover data.

This script is intended to be run after refresh_published_hardcover.py has
already been executed. It targets the remaining published ISBN products that
did not receive any Hardcover data and attempts to populate them from
Titlepage instead.

Set the environment variable INCLUDE_UNPUBLISHED=1 to also backfill all ISBN
products (published and unpublished) that lack Hardcover data.

Run inside an odoo-bin shell, e.g.:

    odoo-bin shell -c /path/to/odoo.conf -d mydb < book_data/scripts/refresh_published_hardcover.py
    INCLUDE_UNPUBLISHED=1 odoo-bin shell -c /path/to/odoo.conf -d mydb < book_data/scripts/refresh_published_hardcover.py

The script iterates over matching products, calls action_refresh_titlepage_data(),
commits after each product, and logs failures per-product without stopping the batch.
"""
import logging
import os
import time
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

# Seconds to sleep between API calls to stay polite to Titlepage.
SLEEP_BETWEEN_CALLS = 0.5

# Skip products refreshed in the last N hours (allows safe resumption).
SKIP_RECENTLY_REFRESHED_HOURS = 168  # 7 days

# Set INCLUDE_UNPUBLISHED=1 to also backfill unpublished ISBN products.
INCLUDE_UNPUBLISHED = os.environ.get('INCLUDE_UNPUBLISHED', '0') == '1'

Product = env['product.template']
cutoff = datetime.utcnow() - timedelta(hours=SKIP_RECENTLY_REFRESHED_HOURS)

# Products with an ISBN barcode that never received any Hardcover data.
domain = [
    '|',
    ('barcode', '=like', '978%'),
    ('barcode', '=like', '979%'),
    ('x_hardcover_reviews_fetch_date', '=', False),
    '|',
    ('x_data_fetch_date', '=', False),
    ('x_data_fetch_date', '<', cutoff),
]
if not INCLUDE_UNPUBLISHED:
    domain = [('is_published', '=', True)] + domain

products = Product.search(domain)

total = len(products)
product_type = "ISBN" if INCLUDE_UNPUBLISHED else "published ISBN"
_logger.info("Found %s %s product(s) with no Hardcover data to backfill from Titlepage", total, product_type)

success = 0
failed = 0
skipped = 0
for idx, product in enumerate(products, start=1):
    if (
        product.x_data_fetch_date
        and product.x_data_fetch_date >= cutoff
    ):
        skipped += 1
        continue

    try:
        _logger.info(
            "[%s/%s] Refreshing Titlepage data for %s (ISBN: %s)",
            idx, total, product.name, product.barcode,
        )
        product.action_refresh_titlepage_data()
        product.write({'x_data_fetch_date': datetime.utcnow()})
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
