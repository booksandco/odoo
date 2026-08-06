import json
import logging

from odoo import api, fields, models

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
        'product.template', string='Product', required=True, index=True, ondelete='cascade',
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

    def _prepare_import_payload(self, template):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return {
            'item_barcode': template.default_code,
            'regular_price': template.list_price,
            'hidden': not template.website_published,
            'stock': max(0, int(template.free_qty)),
            'non_circle_landing_page_url': base_url.rstrip('/') + template.website_url,
        }

    @api.model
    def _enqueue(self, event, templates):
        """Create or refresh pending queue items for the given templates."""
        for template in templates:
            if not template.default_code:
                continue
            if event == 'stock_update':
                payload = {
                    'item_barcode': template.default_code,
                    'stock': max(0, int(template.free_qty)),
                }
            else:
                payload = self._prepare_import_payload(template)
            existing = self.search([
                ('event', '=', event),
                ('product_tmpl_id', '=', template.id),
                ('state', '=', 'pending'),
            ], limit=1)
            if existing:
                existing.write({'payload': json.dumps(payload), 'barcode': template.default_code})
            else:
                self.create({
                    'event': event,
                    'product_tmpl_id': template.id,
                    'barcode': template.default_code,
                    'payload': json.dumps(payload),
                })

    @api.model
    def enqueue_product_import(self, templates):
        self._enqueue('product_import', templates)

    @api.model
    def enqueue_stock_update(self, products):
        """Enqueue a stock update for the templates of the given product variants."""
        templates = products.mapped('product_tmpl_id').filtered('website_published')
        self._enqueue('stock_update', templates)

    @api.model
    def enqueue_full_sync(self):
        """Enqueue a product import (incl. price, stock and landing URL) for
        every product that has a barcode. Published state is carried via the
        'hidden' flag in the payload."""
        templates = self.env['product.template'].search([
            ('default_code', '!=', False),
            ('sale_ok', '=', True),
        ])
        self._enqueue('product_import', templates)
        return len(templates)

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
