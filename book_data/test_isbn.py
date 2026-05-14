#!/usr/bin/env python3
"""Test script to query Hardcover and Titlepage APIs for a given ISBN."""
import json
import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

ISBN = '9780571396689'
HARDCOVER_API_URL = 'https://api.hardcover.app/v1/graphql'
TITLEPAGE_API_URL = 'https://report.titlepage.com/ReST/v1/onix-full'
ONIX_NS = '{http://ns.editeur.org/onix/3.1/reference}'

HARDCOVER_QUERY = """
query GetBookByISBN($isbn: String!) {
    editions(where: { isbn_13: { _eq: $isbn } }) {
        isbn_13 isbn_10 title subtitle edition_format pages release_date
        edition_information cached_image
        publisher { name }
        language { language }
        country { name }
        book {
            title description cached_image cached_tags
            contributions { contribution author { name } }
        }
    }
}
"""


def query_hardcover():
    key = os.environ.get('HARDCOVER_API_KEY')
    if not key:
        print('⚠ HARDCOVER_API_KEY not set, skipping')
        return
    resp = requests.post(
        HARDCOVER_API_URL,
        json={'query': HARDCOVER_QUERY, 'variables': {'isbn': ISBN}},
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if 'errors' in data:
        print(f'Hardcover errors: {data["errors"]}')
        return
    editions = data.get('data', {}).get('editions', [])
    if not editions:
        print('Hardcover: no editions found')
        return
    print('=== HARDCOVER ===')
    print(json.dumps(editions[0], indent=2))


def debug_titlepage_titles(product):
    """Print all TitleDetail blocks to debug title parsing."""
    print('\n--- Title Debugging ---')
    for td in product.findall(f'{ONIX_NS}DescriptiveDetail/{ONIX_NS}TitleDetail'):
        tt = td.find(f'{ONIX_NS}TitleType')
        print(f"TitleDetail (Type={tt.text if tt is not None else 'None'})")
        for te in td.findall(f'{ONIX_NS}TitleElement'):
            level = te.find(f'{ONIX_NS}TitleElementLevel')
            print(f"  TitleElement (Level={level.text if level is not None else 'None'})")
            for child in te:
                tag = child.tag.replace(ONIX_NS, '')
                print(f"    {tag}: {child.text!r}")


def query_titlepage():
    token = os.environ.get('TITLEPAGE_API_TOKEN')
    if not token:
        print('⚠ TITLEPAGE_API_TOKEN not set, skipping')
        return
    resp = requests.get(
        f'{TITLEPAGE_API_URL}/{ISBN}',
        headers={'Authorization': f'Token {token}'},
        timeout=15,
        allow_redirects=True,
    )
    if resp.status_code == 404:
        print('Titlepage: not found')
        return
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    product = root.find(f'{ONIX_NS}Product')
    if product is None:
        print('Titlepage: no Product element')
        return
    print('\n=== TITLEPAGE ===')
    print(ET.tostring(product, encoding='unicode'))
    debug_titlepage_titles(product)


if __name__ == '__main__':
    query_hardcover()
    query_titlepage()
