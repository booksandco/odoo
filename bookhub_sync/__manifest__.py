{
    'name': 'BookHub Sync (CirclePOS)',
    'version': '19.0.1.0.1',
    'category': 'Website',
    'summary': 'Push product and stock updates to the CirclePOS shadow site for BookHub',
    'description': """
Pushes product and stock updates to the CirclePOS shadow site
(bco.circlepos.com) via the CirclePOS API. BookHub reads stock levels,
titles and the redirect slug (non_circle_landing_page_url) from the
shadow site.

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
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
