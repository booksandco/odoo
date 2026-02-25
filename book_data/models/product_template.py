import base64
import logging
import requests

from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

HARDCOVER_API_URL = 'https://api.hardcover.app/v1/graphql'

HARDCOVER_EDITION_QUERY = """
query GetBookByISBN($isbn: String!) {
    editions(where: { isbn_13: { _eq: $isbn } }) {
        isbn_13
        title
        release_date
        cached_image
        book {
            title
            description
            publisher_id
            publisher {
                name
            }
            contributions {
                author {
                    name
                }
            }
        }
    }
}
"""


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def action_fetch_hardcover_data(self):
        """Fetch book metadata from Hardcover API and populate product fields."""
        self.ensure_one()

        isbn = self.barcode
        if not isbn or not isbn.startswith('978'):
            raise UserError(_("A valid ISBN-13 (starting with 978) is required to fetch data from Hardcover."))

        api_key = self.env['ir.config_parameter'].sudo().get_param('book_data.hardcover_api_key')
        if not api_key:
            raise UserError(_("Hardcover API key is not configured. Go to Settings > Inventory > Book Data to set it."))

        edition = self._hardcover_fetch_edition(isbn, api_key)
        if not edition:
            raise UserError(_("No book found on Hardcover for ISBN %s.", isbn))

        vals = self._hardcover_parse_edition(edition)
        if vals:
            self.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Hardcover"),
                'message': _("Book data fetched successfully."),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def _hardcover_fetch_edition(self, isbn, api_key):
        """Fetch edition data from Hardcover GraphQL API."""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
        try:
            response = requests.post(
                HARDCOVER_API_URL,
                json={'query': HARDCOVER_EDITION_QUERY, 'variables': {'isbn': isbn}},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            editions = data.get('data', {}).get('editions', [])
            return editions[0] if editions else None
        except requests.RequestException:
            _logger.exception("Hardcover API request failed for ISBN %s", isbn)
            raise UserError(_("Failed to connect to Hardcover API. Please try again later."))

    def _hardcover_parse_edition(self, edition):
        """Parse Hardcover edition response into product field values."""
        vals = {}
        book = edition.get('book') or {}

        # Title
        title = edition.get('title') or book.get('title')
        if title and not self.name:
            vals['name'] = title

        # Description (HTML field - wrap plain text in <p> tag)
        description = book.get('description')
        if description and not self.description_ecommerce:
            vals['description_ecommerce'] = f'<p>{description}</p>'

        # Author
        contributions = book.get('contributions') or []
        authors = [c['author']['name'] for c in contributions if c.get('author', {}).get('name')]
        if authors and not self.x_author:
            vals['x_author'] = ', '.join(authors)

        # Publisher
        publisher = book.get('publisher')
        if publisher and isinstance(publisher, dict):
            publisher_name = publisher.get('name')
            if publisher_name and not self.x_publisher:
                vals['x_publisher'] = publisher_name

        # Publication date
        release_date = edition.get('release_date')
        if release_date and not self.x_publication_date:
            vals['x_publication_date'] = release_date

        # Image
        cached_image = edition.get('cached_image')
        if cached_image and isinstance(cached_image, dict):
            image_url = cached_image.get('url')
            if image_url and not self.image_1920:
                image_data = self._hardcover_download_image(image_url)
                if image_data:
                    vals['image_1920'] = image_data

        return vals

    @api.model
    def _hardcover_download_image(self, url):
        """Download an image from URL and return base64-encoded data."""
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return base64.b64encode(response.content).decode('utf-8')
        except requests.RequestException:
            _logger.warning("Failed to download image from %s", url)
            return None
