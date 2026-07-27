from django.urls import path

from . import views

app_name = 'atlas'

urlpatterns = [
    path('customers/',            views.customers,        name='customers'),
    path('customers/<int:pk>/',   views.customers,        name='customer_edit'),
    path('products/',             views.products,         name='products'),
    path('products/<int:pk>/',    views.products,         name='product_edit'),
    path('quotations/',           views.quotations,       name='quotations'),
    path('quotations/new/',       views.quotation_new,    name='quotation_new'),
    path('quotations/<int:pk>/',  views.quotation_detail, name='quotation_detail'),
    path('quotations/<int:pk>/status/', views.quotation_status, name='quotation_status'),
    path('api/products-priced/',  views.api_products_priced, name='api_products_priced'),
    # Phase 2 — supply chain
    path('rfqs/',                 views.rfqs,             name='rfqs'),
    path('purchase-orders/',      views.purchase_orders,  name='purchase_orders'),
    path('forecast/',             views.forecast,         name='forecast'),
    # Phase 3 — finance & billing
    path('invoices/',             views.invoices,         name='invoices'),
    path('invoices/create/',      views.invoice_create,   name='invoice_create'),
    path('invoices/<int:pk>/',    views.invoice_detail,   name='invoice_detail'),
    path('ar-aging/',             views.ar_aging_page,    name='ar_aging'),
]
