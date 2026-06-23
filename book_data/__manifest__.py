{
    'name': 'Book Data',
    'version': '19.0.1.3.3',
    'category': 'Retail',
    'summary': 'Fetch book metadata and reviews from external APIs (Hardcover, Titlepage)',
    'description': """
Integrates with Hardcover and Titlepage APIs to fetch book metadata when ISBN is entered.
Populates description, author, image, publication date, and NZ pricing on products.
Also fetches Hardcover ratings and reviews for display on the eCommerce product page.
Periodically updates book data via scheduled tasks to ensure information remains current.
    """,
    'depends': [
        'bookstore',
        'website_sale',
    ],
    'data': [
        'data/ir_cron.xml',
        'views/product_template_views.xml',
        'views/shop_listing.xml',
        'views/website_product_reviews.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
    ],

    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
