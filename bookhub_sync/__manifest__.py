{
    'name': 'BookHub Sync (CirclePOS)',
    'version': '19.0.1.3.0',
    'category': 'Website',
    'summary': 'Push product and stock updates to the CirclePOS shadow site for BookHub',
    'description': """
Pushes product and stock updates to the CirclePOS shadow site
(bco.circlepos.com) via the CirclePOS API. BookHub reads stock levels,
titles and the redirect slug (non_circle_landing_page_url) from the
shadow site.

- Only books are synced (ISBN-13 barcodes starting 978/979). Products
  enter the sync once they have stock; after the first successful push
  they keep syncing. Zero-stock products are hidden on Circle and
  un-hidden automatically on restock.
- Automation rules enqueue changes on product.template and stock.quant.
- A queue model batches changes; a cron flushes them to the API
  (PATCH /v1/site_items for stock, POST /v1/site_products/imports for
  product data).
- OAuth2 client_credentials authentication, configured in
  Settings > General Settings > Integrations.
""",
    'depends': [
        'base_setup',
        'website_sale',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/base_automation.xml',
        'data/ir_actions_server.xml',
        'data/ir_cron.xml',
        'views/bookhub_sync_queue_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
