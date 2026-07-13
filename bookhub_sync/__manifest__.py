{
    'name': 'BookHub Sync',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Send product and stock webhooks to the BookHub/Little Ventures endpoint',
    'description': """
BookHub Sync
============

Scaffold module that notifies an external BookHub/Little Ventures webhook when
product or inventory data changes in Odoo.

The webhook URL is configured in Settings > General Settings > BookHub Sync.
Until the receiver endpoint and payload structure are finalised, the module
ships with a placeholder payload that is easy to adjust.
""",
    'depends': [
        'base_automation',
        'product',
        'stock',
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_actions_server.xml',
        'data/base_automation.xml',
        'views/res_config_settings_views.xml',
    ],
    'license': 'OEEL-1',
    'author': 'Harry Bird',
    'installable': True,
}
