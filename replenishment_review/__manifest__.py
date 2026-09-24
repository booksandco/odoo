{
    'name': 'Replenishment Review',
    'version': '19.0.1.0.3',
    'category': 'Inventory',
    'summary': 'Card-based, keyboard-driven replenishment review',
    'depends': [
        'stock',
        'bookstore',
        'book_data',
        'customer_to_order',
    ],
    'data': [
        'views/replenishment_review_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'replenishment_review/static/src/js/**/*',
            'replenishment_review/static/src/xml/**/*',
            'replenishment_review/static/src/scss/**/*',
        ],
    },
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
