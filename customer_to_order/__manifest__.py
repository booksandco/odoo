{
    'name': 'Customer to Order',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'depends': [
        'bookstore',
        'sale_stock',
        'purchase_stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/customer_order_views.xml',
    ],
    'license': 'LGPL-3',
    'author': 'Harry Bird',
}
