{
    'name': 'Sale Order KPI Dashboard (Odoo 19)',
    'version': '19.0.3.1.0',
    'category': 'Sales',
    'summary': 'Strategic KPIs: Margin, Credit Risk, DSO, Lead Time, Lot Fragmentation, Order Health Score',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'depends': ['sale', 'sale_management', 'stock', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/sale_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_order_kpi_dashboard/static/src/css/kpi_dashboard.css',
            'sale_order_kpi_dashboard/static/src/js/kpi_dashboard.js',
            'sale_order_kpi_dashboard/static/src/js/animated_number_full_currency.js',
            'sale_order_kpi_dashboard/static/src/xml/kpi_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}