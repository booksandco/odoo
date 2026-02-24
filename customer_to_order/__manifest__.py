{
    'name': 'Customer to Order',
    'version': '1.0',
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
    'license': 'OEEL-1',
    'author': 'Odoo S.A.',
}
