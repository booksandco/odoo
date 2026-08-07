import logging
import time

import requests

from odoo import models
from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class CircleApi(models.AbstractModel):
    _name = 'bookhub.circle.api'
    _description = 'CirclePOS API Client'

    def _get_param(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param(key, default)

    def _get_base_url(self):
        return self._get_param('bookhub_sync.base_url', 'https://bco.circlepos.com').rstrip('/')

    def _get_token(self):
        """Return a valid OAuth2 access token, fetching a new one if needed."""
        ICP = self.env['ir.config_parameter'].sudo()
        token = ICP.get_param('bookhub_sync.oauth_token')
        expiry = float(ICP.get_param('bookhub_sync.oauth_token_expiry', 0))
        if token and time.time() < expiry - 60:
            return token

        client_id = ICP.get_param('bookhub_sync.client_id')
        client_secret = ICP.get_param('bookhub_sync.client_secret')
        if not client_id or not client_secret:
            raise UserError(_('BookHub Sync: OAuth2 client credentials are not configured.'))

        response = requests.post(
            f'{self._get_base_url()}/oauth/token',
            json={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'p_r p_m',
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        token = data['access_token']
        ICP.set_param('bookhub_sync.oauth_token', token)
        ICP.set_param(
            'bookhub_sync.oauth_token_expiry',
            str(time.time() + int(data.get('expires_in', 7200))),
        )
        return token

    def _request(self, method, path, payload=None):
        """Perform an authenticated request against the CirclePOS API."""
        url = f'{self._get_base_url()}/api{path}'
        headers = {
            'Authorization': f'Bearer {self._get_token()}',
            'Content-Type': 'application/json',
        }
        site_domain = self._get_param('bookhub_sync.site_domain')
        if site_domain:
            headers['x-site-domain'] = site_domain
        response = requests.request(
            method, url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        return response

    def bulk_update_stock(self, items):
        """PATCH /v1/site_items — up to 100 {item_barcode, stock} dicts."""
        return self._request('PATCH', '/v1/site_items', payload=items)

    def import_products(self, items):
        """POST /v1/site_products/imports — up to 1000 product dicts."""
        return self._request('POST', '/v1/site_products/imports', payload=items)

    def get_exported_isbns(self):
        """GET /v1/data_export/products — ISBNs of every product on the site."""
        response = self._request('GET', '/v1/data_export/products?response_format=json')
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            data = data.get('data', [])
        return {str(item.get('isbn', '')) for item in data}
