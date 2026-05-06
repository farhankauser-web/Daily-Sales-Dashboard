from django.urls import path
from . import views

app_name = 'amazon_api'

urlpatterns = [
    path('',                      views.config_list,       name='list'),
    path('create/',               views.config_form,       name='create'),
    path('<int:pk>/',             views.config_form,       name='edit'),
    path('<int:pk>/test/',        views.test_connection,   name='test'),
    path('anthropic/',            views.anthropic_config,  name='anthropic'),
    path('data/',                 views.fetch_dashboard_data, name='data'),
]
