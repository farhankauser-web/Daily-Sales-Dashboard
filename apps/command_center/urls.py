from django.urls import path
from . import views

app_name = 'command_center'

urlpatterns = [
    path('',                 views.command_center,   name='home'),
    path('api/widget/',      views.api_widget,       name='api_widget'),
    path('api/layout/save/', views.api_save_layout,  name='api_save_layout'),
    path('api/layout/reset/', views.api_reset_layout, name='api_reset_layout'),
]
