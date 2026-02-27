import base64
import logging
import math
import xml.etree.ElementTree as ET

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TITLEPAGE_API_URL = 'https://report.titlepage.com/ReST/v1/onix-full'
ONIX_NS = '{http://ns.editeur.org/onix/3.1/reference}'

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

    x_is_isbn = fields.Boolean(compute='_compute_is_isbn')

    @api.depends('barcode')
    def _compute_is_isbn(self):
        for rec in self:
            rec.x_is_isbn = bool(rec.barcode and rec.barcode.startswith(('978', '979')))

    @api.onchange('barcode')
    def _onchange_barcode_fetch_book_data(self):
        """Automatically fetch book data from Hardcover and Titlepage when ISBN barcode is entered."""
        if not self.barcode or not self.barcode.startswith(('978', '979')):
            return
        
        # Copy barcode to internal reference field
        if self.barcode and not self.default_code:
            self.default_code = self.barcode

        all_vals = {}
        sources = []
        config = self.env['ir.config_parameter'].sudo()

        # Try Hardcover
        hardcover_key = config.get_param('book_data.hardcover_api_key')
        if hardcover_key:
            try:
                edition = self._hardcover_fetch_edition(self.barcode, hardcover_key)
                if edition:
                    vals = self._hardcover_parse_edition(edition)
                    if vals:
                        all_vals.update(vals)
                        sources.append('Hardcover')
            except Exception as e:
                _logger.warning("Failed to fetch Hardcover data for ISBN %s: %s", self.barcode, e)

        # Try Titlepage
        titlepage_token = config.get_param('book_data.titlepage_api_token')
        if titlepage_token:
            try:
                product_xml = self._titlepage_fetch_product(self.barcode, titlepage_token)
                if product_xml is not None:
                    # Apply Hardcover vals first so Titlepage only fills gaps
                    if all_vals:
                        self.update(all_vals)
                    vals = self._titlepage_parse_product(product_xml)
                    if vals:
                        all_vals.update(vals)
                        sources.append('Titlepage')
            except Exception as e:
                _logger.warning("Failed to fetch Titlepage data for ISBN %s: %s", self.barcode, e)

        if not hardcover_key and not titlepage_token:
            return {
                'warning': {
                    'title': _('Book Data APIs Not Configured'),
                    'message': _('Configure API keys in Settings > Inventory > Barcode to auto-fetch book data.'),
                }
            }

        if all_vals:
            self.update(all_vals)

    def action_refresh_book_data(self):
        """Button action to refresh book data from external APIs, overwriting existing values."""
        self.ensure_one()
        if not self.barcode or not self.barcode.startswith(('978', '979')):
            raise UserError(_('A valid ISBN barcode (starting with 978 or 979) is required to fetch book data.'))

        hardcover_vals = {}
        titlepage_vals = {}
        sources = []
        config = self.env['ir.config_parameter'].sudo()

        hardcover_key = config.get_param('book_data.hardcover_api_key')
        if hardcover_key:
            try:
                edition = self._hardcover_fetch_edition(self.barcode, hardcover_key)
                if edition:
                    hardcover_vals = self._hardcover_parse_edition(edition, force=True)
                    if hardcover_vals:
                        sources.append('Hardcover')
            except Exception as e:
                _logger.warning("Failed to fetch Hardcover data for ISBN %s: %s", self.barcode, e)

        titlepage_token = config.get_param('book_data.titlepage_api_token')
        if titlepage_token:
            try:
                product_xml = self._titlepage_fetch_product(self.barcode, titlepage_token)
                if product_xml is not None:
                    titlepage_vals = self._titlepage_parse_product(product_xml, force=True)
                    if titlepage_vals:
                        sources.append('Titlepage')
            except Exception as e:
                _logger.warning("Failed to fetch Titlepage data for ISBN %s: %s", self.barcode, e)

        if not hardcover_key and not titlepage_token:
            raise UserError(_('Configure API keys in Settings > Inventory > Barcode to auto-fetch book data.'))

        # Titlepage as base, Hardcover overwrites (Hardcover takes priority)
        all_vals = {**titlepage_vals, **hardcover_vals}
        if all_vals:
            self.write(all_vals)

        if sources:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Book Data Refreshed'),
                    'message': _('Updated from %s: %s') % (', '.join(sources), ', '.join(all_vals.keys())),
                    'type': 'success',
                    'sticky': False,
                },
            }

        raise UserError(_('No book data found for ISBN %s.') % self.barcode)

    def action_view_on_hardcover(self):
        """Open Hardcover search page for this product's ISBN in a new tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'https://hardcover.app/search?q={self.barcode}',
            'target': 'new',
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

    def _hardcover_parse_edition(self, edition, force=False):
        """Parse Hardcover edition response into product field values."""
        vals = {}
        book = edition.get('book') or {}

        # Title (with optional subtitle)
        title = edition.get('title') or book.get('title')
        subtitle = edition.get('subtitle')
        if subtitle and title:
            title = f"{title}: {subtitle}"
        if title and (force or not self.name):
            vals['name'] = title

        # Description (HTML field - wrap plain text in <p> tag)
        description = book.get('description')
        if description and (force or not self.description_ecommerce):
            vals['description_ecommerce'] = f'<p>{description}</p>'

        # Author - contributions are in the book
        contributions = book.get('contributions') or []
        authors = [c['author']['name'] for c in contributions if c.get('author', {}).get('name')]
        if authors and (force or not self.x_author):
            vals['x_author'] = ', '.join(authors)

        # Publisher - now at edition level
        publisher = edition.get('publisher')
        if publisher and isinstance(publisher, dict):
            publisher_name = publisher.get('name')
            if publisher_name and (force or not self.x_publisher):
                vals['x_publisher'] = publisher_name

        # Publication date
        release_date = edition.get('release_date')
        if release_date and (force or not self.x_publication_date):
            vals['x_publication_date'] = release_date

        # Image
        cached_image = edition.get('cached_image')
        if cached_image and isinstance(cached_image, dict):
            image_url = cached_image.get('url')
            if image_url and (force or not self.image_1920):
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

    # --- Titlepage (ONIX 3.1) ---

    @api.model
    def _titlepage_fetch_product(self, isbn, token):
        """Fetch ONIX product XML from Titlepage API. Returns an Element or None."""
        isbn_clean = isbn.strip()
        url = f'{TITLEPAGE_API_URL}/{isbn_clean}'
        headers = {'Authorization': f'Token {token}'}
        try:
            _logger.debug("Querying Titlepage API for ISBN: %s", isbn_clean)
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            # Response may be gzip-compressed XML; requests handles decoding
            root = ET.fromstring(response.content)
            product = root.find(f'{ONIX_NS}Product')
            return product
        except requests.RequestException as e:
            _logger.warning("Titlepage API request failed for ISBN %s: %s", isbn_clean, e)
            return None
        except ET.ParseError as e:
            _logger.warning("Failed to parse Titlepage ONIX XML for ISBN %s: %s", isbn_clean, e)
            return None

    def _titlepage_find(self, element, path):
        """Find a child element using ONIX-namespaced path."""
        parts = path.split('/')
        current = element
        for part in parts:
            if current is None:
                return None
            current = current.find(f'{ONIX_NS}{part}')
        return current

    def _titlepage_findall(self, element, path):
        """Find all matching child elements using ONIX-namespaced path."""
        ns_path = '/'.join(f'{ONIX_NS}{p}' for p in path.split('/'))
        return element.findall(ns_path)

    def _titlepage_parse_product(self, product, force=False):
        """Parse ONIX 3.1 Product element into product field values.
        Only sets fields that are not already populated on self (unless force=True)."""
        vals = {}
        descriptive = self._titlepage_find(product, 'DescriptiveDetail')
        collateral = self._titlepage_find(product, 'CollateralDetail')
        publishing = self._titlepage_find(product, 'PublishingDetail')

        # Title
        if descriptive is not None and (force or not self.name):
            for td in self._titlepage_findall(descriptive, 'TitleDetail'):
                title_type = self._titlepage_find(td, 'TitleType')
                if title_type is not None and title_type.text == '01':
                    te = self._titlepage_find(td, 'TitleElement')
                    if te is not None:
                        title_text = self._titlepage_find(te, 'TitleText')
                        subtitle = self._titlepage_find(te, 'Subtitle')
                        if title_text is not None and title_text.text:
                            name = title_text.text
                            if subtitle is not None and subtitle.text:
                                name = f"{name}: {subtitle.text}"
                            vals['name'] = name
                    break

        # Author
        if descriptive is not None and (force or not self.x_author):
            authors = []
            for contrib in self._titlepage_findall(descriptive, 'Contributor'):
                role = self._titlepage_find(contrib, 'ContributorRole')
                name = self._titlepage_find(contrib, 'PersonName')
                if role is not None and role.text == 'A01' and name is not None and name.text:
                    authors.append(name.text)
            if authors:
                vals['x_author'] = ', '.join(authors)

        # Publisher
        if publishing is not None and (force or not self.x_publisher):
            publisher_el = self._titlepage_find(publishing, 'Publisher/PublisherName')
            if publisher_el is not None and publisher_el.text:
                vals['x_publisher'] = publisher_el.text

        # Publication date (role 01 = publication date)
        if publishing is not None and (force or not self.x_publication_date):
            for pd in self._titlepage_findall(publishing, 'PublishingDate'):
                role = self._titlepage_find(pd, 'PublishingDateRole')
                if role is not None and role.text == '01':
                    date_el = self._titlepage_find(pd, 'Date')
                    if date_el is not None and date_el.text:
                        raw = date_el.text
                        # Convert YYYYMMDD to YYYY-MM-DD
                        if len(raw) == 8 and raw.isdigit():
                            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                        vals['x_publication_date'] = raw
                    break

        # Description (TextType 03 = main description)
        if collateral is not None and (force or not self.description_ecommerce):
            for tc in self._titlepage_findall(collateral, 'TextContent'):
                text_type = self._titlepage_find(tc, 'TextType')
                if text_type is not None and text_type.text == '03':
                    text_el = self._titlepage_find(tc, 'Text')
                    if text_el is not None and text_el.text:
                        vals['description_ecommerce'] = text_el.text
                    break

        # Cover image (ResourceContentType 01 = front cover)
        if collateral is not None and (force or not self.image_1920):
            for sr in self._titlepage_findall(collateral, 'SupportingResource'):
                rct = self._titlepage_find(sr, 'ResourceContentType')
                if rct is not None and rct.text == '01':
                    rv = self._titlepage_find(sr, 'ResourceVersion')
                    if rv is not None:
                        link = self._titlepage_find(rv, 'ResourceLink')
                        if link is not None and link.text:
                            image_data = self._hardcover_download_image(link.text)
                            if image_data:
                                vals['image_1920'] = image_data
                    break

        # Weight (MeasureType 08 = weight)
        if descriptive is not None and (force or not self.weight):
            for measure in self._titlepage_findall(descriptive, 'Measure'):
                mtype = self._titlepage_find(measure, 'MeasureType')
                if mtype is not None and mtype.text == '08':
                    measurement = self._titlepage_find(measure, 'Measurement')
                    if measurement is not None and measurement.text:
                        try:
                            grams = float(measurement.text)
                            vals['weight'] = grams / 1000.0
                        except ValueError:
                            pass
                    break

        # NZ supply: list price (PriceType 02, rounded up) and vendor from supplier name
        for ps in self._titlepage_findall(product, 'ProductSupply'):
            market_territory = self._titlepage_find(ps, 'Market/Territory/CountriesIncluded')
            if market_territory is not None and 'NZ' in market_territory.text:
                supply = self._titlepage_find(ps, 'SupplyDetail')
                if supply is not None:
                    # List price
                    for price_el in self._titlepage_findall(supply, 'Price'):
                        price_type = self._titlepage_find(price_el, 'PriceType')
                        if price_type is not None and price_type.text == '02':
                            amount = self._titlepage_find(price_el, 'PriceAmount')
                            if amount is not None and amount.text:
                                try:
                                    price = float(amount.text)
                                    vals['list_price'] = math.ceil(price)
                                except ValueError:
                                    pass
                            break
                    # Vendor from supplier name
                    supplier_name_el = self._titlepage_find(supply, 'Supplier/SupplierName')
                    if supplier_name_el is not None and supplier_name_el.text:
                        self._titlepage_set_vendor(supplier_name_el.text)
                break

        return vals

    def _titlepage_set_vendor(self, supplier_name):
        """Match supplier name to a res.partner and add as vendor if not already present."""
        partner = self.env['res.partner'].search(
            [('name', 'ilike', supplier_name)], limit=1,
        )
        if not partner:
            _logger.info("No partner found matching Titlepage supplier: %s", supplier_name)
            return
        # Check if this partner is already a vendor on the product
        if partner in self.seller_ids.mapped('partner_id'):
            return
        self.update({
            'seller_ids': [(0, 0, {
                'partner_id': partner.id,
                'min_qty': 1,
            })],
        })
