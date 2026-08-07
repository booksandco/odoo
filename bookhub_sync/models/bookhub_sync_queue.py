import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STOCK_CHUNK_SIZE = 100  # PATCH /v1/site_items limit
IMPORT_CHUNK_SIZE = 1000  # POST /v1/site_products/imports limit
MAX_ATTEMPTS = 5
# Bound the work done in a single cron run.
MAX_ITEMS_PER_RUN = 2000


class BookhubSyncQueue(models.Model):
    _name = 'bookhub.sync.queue'
    _description = 'BookHub Sync Queue'
    _order = 'id'

    event = fields.Selection(
        selection=[
            ('product_import', 'Product Import'),
            ('stock_update', 'Stock Update'),
        ],
        required=True,
        index=True,
    )
    product_tmpl_id = fields.Many2one(
        'product.template', string='Product', index=True, ondelete='cascade',
    )
    barcode = fields.Char(index=True)
    payload = fields.Text(help='JSON payload sent to the CirclePOS API.')
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='pending',
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0)
    error = fields.Text()

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    @api.model
    def _get_stock(self, template):
        """Free-to-use quantity across all variants (free_qty lives on
        product.product, not on the template)."""
        return max(0, int(sum(template.product_variant_ids.mapped('free_qty'))))

    @api.model
    def _is_book(self, template):
        """Only books are synced: ISBN-13 barcodes start with 978 or 979."""
        code = template.default_code or ''
        return code.startswith(('978', '979'))

    @api.model
    def _should_sync(self, template):
        """Sync books only, and only once they have stock — unless Circle
        already knows the product, so that a later drop to zero (and price
        or publish changes) still gets pushed."""
        return self._is_book(template) and (
            template.bookhub_synced or self._get_stock(template) > 0
        )

    def _prepare_import_payload(self, template, stock):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return {
            'item_barcode': template.default_code,
            'regular_price': template.list_price,
            # Zero-stock products are hidden to keep the site clean; a
            # restock un-hides them (see _enqueue's zero-crossing rule).
            'hidden': not template.website_published or stock == 0,
            'stock': stock,
            'non_circle_landing_page_url': base_url.rstrip('/') + template.website_url,
        }

    @api.model
    def _enqueue(self, event, templates):
        """Create or refresh pending queue items for the given templates."""
        for template in templates:
            if not self._should_sync(template):
                continue
            stock = self._get_stock(template)
            effective = event
            if event == 'stock_update' and template.bookhub_last_stock >= 0:
                # Crossing zero either way flips the hidden flag, which only
                # a product import carries — a bare stock PATCH cannot.
                if (template.bookhub_last_stock == 0) != (stock == 0):
                    effective = 'product_import'
            template.sudo().bookhub_last_stock = stock
            if effective == 'stock_update':
                payload = {'item_barcode': template.default_code, 'stock': stock}
            else:
                payload = self._prepare_import_payload(template, stock)
            existing = self.search([
                ('event', '=', effective),
                ('product_tmpl_id', '=', template.id),
                ('state', '=', 'pending'),
            ], limit=1)
            if existing:
                existing.write({'payload': json.dumps(payload), 'barcode': template.default_code})
            else:
                self.create({
                    'event': effective,
                    'product_tmpl_id': template.id,
                    'barcode': template.default_code,
                    'payload': json.dumps(payload),
                })

    @api.model
    def _enqueue_raw(self, event, items):
        """Enqueue pre-built payloads.

        items: list of (barcode, payload dict, product.template or None).
        """
        for barcode, payload, template in items:
            existing = self.search([
                ('event', '=', event),
                ('barcode', '=', barcode),
                ('state', '=', 'pending'),
            ], limit=1)
            if existing:
                existing.write({'payload': json.dumps(payload)})
            else:
                self.create({
                    'event': event,
                    'product_tmpl_id': template.id if template else False,
                    'barcode': barcode,
                    'payload': json.dumps(payload),
                })

    @api.model
    def enqueue_product_import(self, templates):
        self._enqueue('product_import', templates)

    @api.model
    def enqueue_stock_update(self, products):
        """Enqueue a stock update for the templates of the given product variants."""
        self._enqueue('stock_update', products.mapped('product_tmpl_id'))

    @api.model
    def enqueue_full_sync(self):
        """Enqueue a product import (incl. price, stock and landing URL) for
        every in-stock book AND every book already on the Circle site, so
        existing products get refreshed (price/hidden/stock/landing URL)
        even when now out of stock. Published state is carried via the
        'hidden' flag in the payload."""
        try:
            circle_isbns = self.env['bookhub.circle.api'].get_exported_isbns()
        except Exception as exc:
            raise UserError(
                'BookHub Sync: could not fetch the product list from '
                f'CirclePOS ({exc}). Check the API credentials in '
                'Settings > General Settings > Integrations.'
            )
        templates = self.env['product.template'].search([
            '|',
            ('default_code', '=like', '978%'),
            ('default_code', '=like', '979%'),
            ('sale_ok', '=', True),
        ])
        on_circle = templates.filtered(lambda t: t.default_code in circle_isbns)
        in_stock = templates.filtered(lambda t: self._get_stock(t) > 0)
        # Products Circle already knows about should keep syncing even at
        # zero stock, so mark them as synced.
        on_circle.sudo().write({'bookhub_synced': True})
        to_sync = on_circle | in_stock
        self._enqueue('product_import', to_sync)
        return len(to_sync)

    @api.model
    def enqueue_cleanup(self):
        """Hide everything on the Circle site that is not an in-stock book:
        non-ISBN merchandise, books we no longer carry, and zero-stock
        books. Hidden products stay known to Circle, so restock updates
        keep working (unlike deletion, which the API does not support)."""
        try:
            circle_isbns = self.env['bookhub.circle.api'].get_exported_isbns()
        except Exception as exc:
            raise UserError(
                'BookHub Sync: could not fetch the product list from '
                f'CirclePOS ({exc}). Check the API credentials in '
                'Settings > General Settings > Integrations.'
            )
        books = self.env['product.template'].search([
            '|',
            ('default_code', '=like', '978%'),
            ('default_code', '=like', '979%'),
        ])
        by_code = {t.default_code: t for t in books}
        items = []
        for isbn in circle_isbns:
            template = by_code.get(isbn) if isbn.startswith(('978', '979')) else None
            if template and self._get_stock(template) > 0:
                continue  # healthy in-stock book, left to the normal sync
            items.append((isbn, {'item_barcode': isbn, 'hidden': True}, template))
            if template:
                # Record zero so a later restock is seen as a zero-crossing
                # and un-hides the product via a product import.
                template.sudo().bookhub_last_stock = 0
        self._enqueue_raw('product_import', items)
        return len(items)

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    @api.model
    def action_view_queue(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'BookHub Sync Queue',
            'res_model': 'bookhub.sync.queue',
            'view_mode': 'list,form',
        }

    @api.model
    def _cron_process_queue(self):
        self._process_queue()
        # Purge processed items older than 7 days.
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=7)
        self.search([('state', '=', 'done'), ('write_date', '<', cutoff)]).unlink()

    def _process_queue(self):
        api = self.env['bookhub.circle.api']
        pending = self.search([('state', '=', 'pending')], limit=MAX_ITEMS_PER_RUN)
        for event, chunk_size in (('stock_update', STOCK_CHUNK_SIZE), ('product_import', IMPORT_CHUNK_SIZE)):
            items = pending.filtered(lambda q, e=event: q.event == e)
            for offset in range(0, len(items), chunk_size):
                self._flush_chunk(api, event, items[offset:offset + chunk_size])

    def _flush_chunk(self, api, event, chunk):
        payloads = [json.loads(q.payload) for q in chunk]
        try:
            if event == 'stock_update':
                response = api.bulk_update_stock(payloads)
            else:
                response = api.import_products(payloads)
        except Exception as exc:
            _logger.exception('BookHub sync: %s request failed', event)
            self._mark_chunk(chunk, exc)
            return

        if response.ok:
            if event == 'product_import':
                try:
                    import_id = response.json().get('id')
                    _logger.info('BookHub sync: product import %s accepted (%d items)', import_id, len(chunk))
                except ValueError:
                    pass
            chunk.write({'state': 'done', 'error': False})
            # Remember that Circle knows these products, so future updates
            # (e.g. stock dropping to zero) keep syncing.
            chunk.mapped('product_tmpl_id').sudo().write({'bookhub_synced': True})
            if event == 'stock_update':
                # PATCH rejects items the site does not list; re-send those
                # as product imports so restocks of removed/missing items
                # self-heal instead of silently failing.
                succeeded = set()
                try:
                    body = response.json()
                    succeeded = {
                        str(i.get('item_barcode'))
                        for i in (body.get('data') or {}).get('success') or []
                    }
                except ValueError:
                    pass
                missing = chunk.filtered(lambda q: q.barcode not in succeeded)
                if missing:
                    _logger.info(
                        'BookHub sync: %d stock update(s) not listed on Circle; '
                        'falling back to product import', len(missing),
                    )
                    self._enqueue('product_import', missing.mapped('product_tmpl_id'))
        else:
            _logger.warning('BookHub sync: %s rejected (%s): %s', event, response.status_code, response.text)
            self._mark_chunk(chunk, f'HTTP {response.status_code}: {response.text[:2000]}')

    def _mark_chunk(self, chunk, error):
        for item in chunk:
            attempts = item.attempts + 1
            item.write({
                'attempts': attempts,
                'state': 'failed' if attempts >= MAX_ATTEMPTS else 'pending',
                'error': str(error)[:2000],
            })
