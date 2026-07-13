import logging
import requests

from odoo import api, models

_logger = logging.getLogger(__name__)


class BookhubWebhook(models.AbstractModel):
    _name = 'bookhub.webhook'
    _description = 'BookHub Webhook Helper'

    def _get_webhook_url(self):
        """Return the configured BookHub webhook URL, or None if not set."""
        return self.env['ir.config_parameter'].sudo().get_param('bookhub_sync.webhook_url')

    def _post_payload(self, payload):
        """POST the payload to the configured endpoint."""
        url = self._get_webhook_url()
        if not url:
            _logger.warning('BookHub webhook URL is not configured; skipping notification.')
            return False

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            _logger.info('BookHub webhook sent successfully: %s', response.status_code)
            return True
        except requests.exceptions.RequestException:
            _logger.exception('Failed to send BookHub webhook to %s', url)
            return False

    @api.model
    def notify_product_template(self, product_template):
        """Notify BookHub that a product template has changed.

        TODO: Adjust the payload structure once Little Ventures provides the
        final endpoint contract.
        """
        payload = {
            'event': 'product_updated',
            'product_id': product_template.id,
            'default_code': product_template.default_code,
            'name': product_template.name,
            'list_price': product_template.list_price,
            'standard_price': product_template.standard_price,
            'website_published': product_template.website_published,
            'website_url': product_template.website_url,
        }
        return self._post_payload(payload)

    @api.model
    def notify_stock_quant(self, stock_quant):
        """Notify BookHub that a stock quantity has changed.

        Only published products are reported.  TODO: Adjust payload once the
        receiver contract is known.
        """
        product_template = stock_quant.product_id.product_tmpl_id
        if not product_template.website_published:
            return False

        payload = {
            'event': 'stock_updated',
            'product_id': product_template.id,
            'default_code': product_template.default_code,
            'name': product_template.name,
            'warehouse_id': stock_quant.warehouse_id.id if stock_quant.warehouse_id else None,
            'warehouse_name': stock_quant.warehouse_id.name if stock_quant.warehouse_id else None,
            'location_id': stock_quant.location_id.id,
            'quantity': stock_quant.quantity,
            'available_quantity': stock_quant.available_quantity,
            'reserved_quantity': stock_quant.reserved_quantity,
        }
        return self._post_payload(payload)
