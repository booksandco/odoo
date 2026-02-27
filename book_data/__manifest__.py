{
    'name': 'Book Data',
    'version': '1.8',
    'category': 'Retail',
    'summary': 'Fetch book metadata from external APIs (Hardcover, Titlepage)',
    'description': """
Integrates with Hardcover and Titlepage APIs to fetch book metadata when ISBN is entered.
Populates description, author, image, publication date, and AU/NZ pricing on products.
    """,
    'depends': [
        'bookstore',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_model_fields.xml',
        'views/product_template_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
