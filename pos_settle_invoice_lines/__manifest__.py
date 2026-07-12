# -*- coding: utf-8 -*-
{
    'name': 'POS Settle Invoice Lines',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Show the invoice product lines when settling an invoice in POS.',
    'depends': ['pos_settle_due'],
    'installable': True,
    'auto_install': False,
    'author': 'Books & Co.',
    'license': 'OEEL-1',
    'data': [],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_settle_invoice_lines/static/src/**/*',
        ],
    },
}
