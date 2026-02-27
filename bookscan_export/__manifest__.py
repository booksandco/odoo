{
    'name': 'BookScan Export',
    'version': '0.2.2',
    'category': 'Retail',
    'summary': 'Weekly POS sales export to Nielsen BookScan via SFTP',
    'description': """
Generates a weekly CSV of book sales from POS orders and uploads it to
Nielsen BookScan's SFTP server.  Includes website sales with post code
and country code when available.
    """,
    'depends': [
        'point_of_sale',
        'sale_management',
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
    ],
    'external_dependencies': {
        'python': ['paramiko'],
    },
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
