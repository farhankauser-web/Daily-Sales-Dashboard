from django.urls import path
from . import views
from . import views_inventory as vi

app_name = 'dashboard'

urlpatterns = [
    path('',                     views.index,              name='index'),
    path('historical/',          views.historical,         name='historical'),
    path('cogs/',                views.cogs,               name='cogs'),
    path('cogs/fba-template/',   views.fba_rates_template_xlsx, name='fba_template'),
    path('targets/',             views.targets,            name='targets'),
    path('targets/template/',    views.targets_template_xlsx, name='targets_template'),
    path('catalog/',             views.catalog,            name='catalog'),
    path('catalog/new/',         views.product_form,       name='product_create'),
    path('catalog/<int:pk>/',    views.product_form,       name='product_edit'),
    path('summary/',             views.executive_summary,  name='summary'),
    path('summary/stream/',      views.summary_stream,     name='summary_stream'),
    path('export/',              views.export_csv,         name='export'),
    path('product-line/',        views.product_line_analysis, name='product_line'),

    # Inventory
    path('inventory/',               vi.inventory,          name='inventory'),
    path('inventory/<int:pk>/history/', vi.inventory_history, name='inventory_history'),
    path('inventory/update/',        vi.inventory_update,   name='inventory_update'),

    # PPC Analytics
    path('ppc/',                     vi.ppc_analytics,      name='ppc'),

    # Alerts
    path('alerts/',                  vi.alerts,             name='alerts'),
    path('alerts/<int:pk>/resolve/', vi.alert_resolve,      name='alert_resolve'),
    path('alerts/api/',              vi.alerts_api,         name='alerts_api'),
]
