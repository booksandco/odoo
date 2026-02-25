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
				isbn_10
				title
				subtitle
				edition_format
				pages
				release_date
				edition_information
				cached_image
				publisher {
					name
				}
				language {
					language
				}
				country {
					name
				}
				book {
					title
					description
					cached_tags
					contributions {
						contribution
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

    @api.onchange('barcode')
    def _onchange_barcode_fetch_hardcover(self):
        """Automatically fetch Hardcover data when ISBN barcode is entered/updated."""
        if not self.barcode or not self.barcode.startswith('978'):
            return

        api_key = self.env['ir.config_parameter'].sudo().get_param('book_data.hardcover_api_key')
        if not api_key:
            return {
                'warning': {
                    'title': _('Hardcover API Not Configured'),
                    'message': _('Configure your Hardcover API key in Settings > Inventory > Barcode to auto-fetch book data.'),
                }
            }

        try:
            edition = self._hardcover_fetch_edition(self.barcode, api_key)
            if not edition:
                return {
                    'warning': {
                        'title': _('Book Not Found'),
                        'message': _('No book found on Hardcover for ISBN %s.') % self.barcode,
                    }
                }

            vals = self._hardcover_parse_edition(edition)
            if vals:
                self.update(vals)
                populated_fields = ', '.join(vals.keys())
                return {
                    'warning': {
                        'title': _('Book Data Fetched'),
                        'message': _('Successfully populated: %s') % populated_fields,
                    }
                }
        except UserError as e:
            _logger.warning(f"Failed to fetch Hardcover data for ISBN {self.barcode}: {e}")
            return {
                'warning': {
                    'title': _('Hardcover API Error'),
                    'message': _('Failed to fetch data from Hardcover API. Please try again later.'),
                }
            }
        except Exception as e:
            _logger.warning(f"Unexpected error fetching Hardcover data: {e}")
            return {
                'warning': {
                    'title': _('Error'),
                    'message': _('An unexpected error occurred while fetching book data.'),
                }
            }

    @api.model
    def _hardcover_fetch_edition(self, isbn, api_key):
        """Fetch edition data from Hardcover GraphQL API."""
        # Strip whitespace from ISBN
        isbn_clean = isbn.strip()
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
        try:
            _logger.debug(f"Querying Hardcover API for ISBN: {isbn_clean}")
            response = requests.post(
                HARDCOVER_API_URL,
                json={'query': HARDCOVER_EDITION_QUERY, 'variables': {'isbn': isbn_clean}},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            
            # Log the response for debugging
            if 'errors' in data:
                _logger.warning(f"Hardcover API errors for ISBN {isbn_clean}: {data['errors']}")
                return None
            
            editions = data.get('data', {}).get('editions', [])
            _logger.debug(f"Hardcover API returned {len(editions)} editions for ISBN {isbn_clean}")
            if editions:
                edition_isbn = editions[0].get('isbn_13') or editions[0].get('isbn')
                _logger.debug(f"First edition found with ISBN: {edition_isbn}")
            return editions[0] if editions else None
        except requests.RequestException as e:
            _logger.exception("Hardcover API request failed for ISBN %s: %s", isbn_clean, str(e))
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
