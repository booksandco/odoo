# Testing Plan

This document outlines the testing strategy for each custom module. All tests should be implemented under each module's `tests/` directory and committed alongside code changes. odoo.sh runs tests automatically on push.

## Module: `bookstore`

### Product Template
- **ISBN compute**: Create products with barcodes starting with `978`, `979`, and other prefixes. Assert `x_is_isbn` is computed correctly.
- **Category sync**: Change a product's `categ_id` and assert the matching `product.public.category` is added while preserving others.
- **Last sale date**: Create a confirmed outgoing stock move for a product variant. Assert `x_last_sale_date` matches the move date.
- **Last vendor**: Set a supplier on a product. Assert `x_last_vendor` resolves to the supplier's partner.

### Sale Order
- **Shipping validation**: Create a sale order with physical products and no carrier. Assert `_confirmation_error_message` returns the shipping required error. Assert orders with services only or with a carrier return no error.

### Loyalty
- **Gift card expiry**: Create a loyalty program with `card_validity_months = 6`. Create a gift card and assert the expiration date is ~6 months in the future.

### Replenishment
- **Total sales**: Create a confirmed sale order. Open the reordering rules (orderpoint) for the product and assert `x_total_sales` equals the sold quantity.

---

## Module: `book_data`

### ISBN Lookup (`_onchange_barcode_fetch_book_data`)
- Mock `requests.post` for Hardcover and `requests.get` for Titlepage.
- **Happy path**: Enter a valid ISBN barcode. Assert product name, author, publisher, and image are populated.
- **API unconfigured**: Clear config parameters. Assert a warning is returned.
- **Not found**: Mock 404/empty response. Assert no exception is raised and a warning is shown.
- **Partial data**: Hardcover returns data but Titlepage does not (and vice versa). Assert available fields are populated.

### Score Computation
- Create a product with varying levels of data completeness. Assert `x_data_score` matches the weighted sum.

### Cron (`_cron_refresh_book_data`)
- Create multiple ISBN products with different scores. Assert the cron picks the lowest-score product that has `x_data_fetch_date = False`.

### Refresh Action (`action_refresh_book_data`)
- Populate a product, then call the action with `force=True` (via the refresh button). Assert existing data is overwritten.

---

## Module: `bookscan_export`

### CSV Generation
- Create POS and sale orders with book barcodes in a date range. Call `_build_csv` and assert:
  - Header row is present.
  - ISBNs match the expected format.
  - Quantities are integers.
  - Discounted prices are calculated correctly (`price_unit * (1 - discount/100)`).
  - Dates are formatted as `%Y%m%d`.

### Empty Week
- Run `_run_export` for a week with no book sales. Assert a log record is created with `record_count = 0` and `state = 'success'`.

### SFTP Upload (Mocked)
- Mock `paramiko.Transport`. Call `_sftp_upload` and assert the transport connects, `sftp.put` is called with the correct filename, and the temp file is cleaned up.

### Timezone Handling
- Set the config parameter to `Pacific/Auckland`. Create orders near midnight NZ time. Assert the date range logic uses the correct local date.

---

## Module: `customer_to_order`

### SQL View Status Mapping
- Seed data: sale order line for a product, with varying stock/purchase states.
- **Available**: Free qty >= qty_to_deliver. Assert status is `available`.
- **On order**: Open confirmed PO exists. Assert status is `on_order`.
- **In cart**: Draft/sent PO exists. Assert status is `in_cart`.
- **Unordered**: No stock and no PO. Assert status is `unordered`.

### Create PO Action
- Select multiple `unordered` lines with the same vendor. Click **Order**. Assert a single draft PO is created with the correct lines and quantities.
- Select lines with different vendors. Assert separate POs are created per vendor.
- Select lines with no vendor. Assert `UserError` is raised with the product names listed.

---

## Module: `partner_mailing`

### Auto-Subscribe
- Configure `partner_mailing.default_list_id` to a mailing list.
- Create a new contact with an email. Assert a `mailing.subscription` record is created linking the contact to the list.
- Create a contact without an email. Assert no subscription is created.
- Unset the config parameter. Create a contact. Assert no subscription is created.

---

## Module: `vendor_returns`

### Order Lifecycle
- Create a return order. Assert state is `draft` and name is not `New`.
- `action_mark_sent()`. Assert state is `sent`.
- `action_confirm()`. Assert state is `confirmed`.
- `action_cancel()`. Assert state is `cancel`.
- `action_draft()`. Assert state is `draft`.

### Invalid Transitions
- Attempt to confirm from `draft`. Assert `UserError`.
- Attempt to draft from `confirmed`. Assert `UserError`.

### Picking Generation
- Add a line to a confirmed order. Call `action_generate_picks()`. Assert `picking_ids` is populated and stock moves are created.

### Invoice Creation
- Attempt to create an invoice before picks are done. Assert `UserError`.
- Validate the picking, then create an invoice. Assert a vendor credit note is generated.

### Copy
- Create and confirm an order with pickings and invoices. Duplicate it. Assert the copy has no pickings, no invoices, and no move lines.

---

## Module: `web_search`

### Search Fields
- Call `_search_get_detail` on `product.template`. Assert the returned dict contains `x_author` and `x_publisher` in `search_fields` and `fetch_fields`.
