from django.urls import path

from . import views

app_name = 'walmart_mcf'

urlpatterns = [
    path('',              views.walmart_orders,      name='orders'),
    path('api/orders/',   views.api_walmart_orders,  name='api_orders'),
    path('api/run/',      views.api_walmart_run,     name='api_run'),
    path('api/reprocess/', views.api_walmart_reprocess, name='api_reprocess'),
    path('export.xlsx',   views.walmart_export_xlsx, name='export_xlsx'),
    path('config/',       views.walmart_config,      name='config'),
    path('config/test/',  views.walmart_config_test, name='config_test'),
]
