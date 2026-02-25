{
    'name': 'Book Data',
    'version': '1.3',
    'category': 'Retail',
    'summary': 'Fetch book metadata from external APIs (Hardcover)',
    'description': """
Integrates with Hardcover API to fetch book metadata when ISBN is entered.
Populates description, author, image, and publication date on products.
    """,
    'depends': [
        'bookstore',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_model_fields.xml',
        'views/res_config_settings_views.xml',
    ],
    'license': 'OEEL-1',
    'author': 'Odoo S.A.',
}
