{
    'name': 'Book Data',
    'version': '1.11.2',
    'category': 'Retail',
    'summary': 'Fetch book metadata from external APIs (Hardcover, Titlepage)',
    'description': """
Integrates with Hardcover and Titlepage APIs to fetch book metadata when ISBN is entered.
Populates description, author, image, publication date, and NZ pricing on products.
Periodically updates book data via scheduled tasks to ensure information remains current.
    """,
    'depends': [
        'bookstore',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_model_fields.xml',
        'data/ir_cron.xml',
        'views/product_template_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': '_post_init_hook',
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
