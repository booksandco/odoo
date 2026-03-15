import base64
import logging
import math
import xml.etree.ElementTree as ET

import requests

from odoo import api, models, _
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
					cached_image
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
            except Exception:
                _logger.exception("Failed to fetch Titlepage data for ISBN %s", self.barcode)

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
            except Exception:
                _logger.exception("Failed to fetch Titlepage data for ISBN %s", self.barcode)

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

        # Image (try edition first, fall back to book-level image)
        image_url = None
        cached_image = edition.get('cached_image')
        if cached_image and isinstance(cached_image, dict):
            image_url = cached_image.get('url')
        if not image_url:
            book_image = book.get('cached_image')
            if book_image and isinstance(book_image, dict):
                image_url = book_image.get('url')
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
            root = ET.fromstring(response.content)
            return root.find(f'{ONIX_NS}Product')
        except requests.RequestException as e:
            _logger.warning("Titlepage API request failed for ISBN %s: %s", isbn_clean, e)
            return None
        except ET.ParseError as e:
            _logger.warning("Failed to parse Titlepage ONIX XML for ISBN %s: %s", isbn_clean, e)
            return None

    @staticmethod
    def _titlepage_find(element, path):
        """Find a child element using ONIX-namespaced path."""
        parts = path.split('/')
        current = element
        for part in parts:
            if current is None:
                return None
            current = current.find(f'{ONIX_NS}{part}')
        return current

    @staticmethod
    def _titlepage_findall(element, path):
        """Find all matching child elements using ONIX-namespaced path."""
        ns_path = '/'.join(f'{ONIX_NS}{p}' for p in path.split('/'))
        return element.findall(ns_path)

    def _titlepage_parse_product(self, product, force=False):
        """Parse ONIX Product element into product field values.
        Only sets fields that are not already populated on self (unless force=True)."""
        vals = {}
        _find = self._titlepage_find
        _findall = self._titlepage_findall
        descriptive = _find(product, 'DescriptiveDetail')
        collateral = _find(product, 'CollateralDetail')
        publishing = _find(product, 'PublishingDetail')

        # Title
        if descriptive is not None and (force or not self.name):
            for td in _findall(descriptive, 'TitleDetail'):
                title_type = _find(td, 'TitleType')
                if title_type is not None and title_type.text == '01':
                    te = _find(td, 'TitleElement')
                    if te is not None:
                        title_text = _find(te, 'TitleText')
                        subtitle = _find(te, 'Subtitle')
                        if title_text is not None and title_text.text:
                            name = title_text.text
                            if subtitle is not None and subtitle.text:
                                name = f"{name}: {subtitle.text}"
                            vals['name'] = name
                    break

        # Author
        if descriptive is not None and (force or not self.x_author):
            authors = []
            for contrib in _findall(descriptive, 'Contributor'):
                role = _find(contrib, 'ContributorRole')
                if role is None or role.text != 'A01':
                    continue
                name_el = _find(contrib, 'PersonName')
                if name_el is not None and name_el.text:
                    authors.append(name_el.text)
                    continue
                # Fall back to PersonNameInverted ("Last, First" -> "First Last")
                inverted = _find(contrib, 'PersonNameInverted')
                if inverted is not None and inverted.text:
                    parts = [p.strip() for p in inverted.text.split(',', 1)]
                    authors.append(' '.join(reversed(parts)) if len(parts) == 2 else inverted.text)
                    continue
                # Fall back to NamesBeforeKey + KeyNames
                before = _find(contrib, 'NamesBeforeKey')
                key = _find(contrib, 'KeyNames')
                if key is not None and key.text:
                    full = f"{before.text} {key.text}" if before is not None and before.text else key.text
                    authors.append(full)
            if authors:
                vals['x_author'] = ', '.join(authors)

        # Publisher
        if publishing is not None and (force or not self.x_publisher):
            publisher_el = _find(publishing, 'Publisher/PublisherName')
            if publisher_el is not None and publisher_el.text:
                vals['x_publisher'] = publisher_el.text

        # Publication date (role 01 = publication date)
        if publishing is not None and (force or not self.x_publication_date):
            for pd in _findall(publishing, 'PublishingDate'):
                role = _find(pd, 'PublishingDateRole')
                if role is not None and role.text == '01':
                    date_el = _find(pd, 'Date')
                    if date_el is not None and date_el.text:
                        raw = date_el.text
                        # Convert YYYYMMDD to YYYY-MM-DD
                        if len(raw) == 8 and raw.isdigit():
                            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                        vals['x_publication_date'] = raw
                    break

        # Description (TextType 03 = main description)
        if collateral is not None and (force or not self.description_ecommerce):
            for tc in _findall(collateral, 'TextContent'):
                text_type = _find(tc, 'TextType')
                if text_type is not None and text_type.text == '03':
                    text_el = _find(tc, 'Text')
                    if text_el is not None and text_el.text:
                        vals['description_ecommerce'] = text_el.text
                    break

        # Cover image (ResourceContentType 01 = front cover)
        if collateral is not None and (force or not self.image_1920):
            for sr in _findall(collateral, 'SupportingResource'):
                rct = _find(sr, 'ResourceContentType')
                if rct is not None and rct.text == '01':
                    rv = _find(sr, 'ResourceVersion')
                    if rv is not None:
                        link = _find(rv, 'ResourceLink')
                        if link is not None and link.text:
                            image_data = self._hardcover_download_image(link.text)
                            if image_data:
                                vals['image_1920'] = image_data
                    break

        # Weight (MeasureType 08 = weight)
        if descriptive is not None and (force or not self.weight):
            for measure in _findall(descriptive, 'Measure'):
                mtype = _find(measure, 'MeasureType')
                if mtype is not None and mtype.text == '08':
                    measurement = _find(measure, 'Measurement')
                    if measurement is not None and measurement.text:
                        try:
                            grams = float(measurement.text)
                            vals['weight'] = grams / 1000.0
                        except ValueError:
                            pass
                    break

        # NZ supply: list price (PriceType 02, rounded up) and vendor from supplier name
        for ps in _findall(product, 'ProductSupply'):
            market_territory = _find(ps, 'Market/Territory/CountriesIncluded')
            if market_territory is not None and market_territory.text and 'NZ' in market_territory.text:
                supply = _find(ps, 'SupplyDetail')
                if supply is not None:
                    # List price (NZD only, PriceType 02 = RRP inc tax)
                    for price_el in _findall(supply, 'Price'):
                        price_type = _find(price_el, 'PriceType')
                        currency = _find(price_el, 'CurrencyCode')
                        if (price_type is not None and price_type.text == '02'
                                and currency is not None and currency.text == 'NZD'):
                            amount = _find(price_el, 'PriceAmount')
                            if amount is not None and amount.text:
                                try:
                                    price = float(amount.text)
                                    vals['list_price'] = math.ceil(price)
                                except ValueError:
                                    pass
                            break
                    # Vendor from supplier name
                    supplier_name_el = _find(supply, 'Supplier/SupplierName')
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
