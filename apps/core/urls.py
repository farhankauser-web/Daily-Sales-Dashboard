"""apps/core/urls.py + views.py"""
from django.urls import path
from django.shortcuts import redirect

app_name = 'core'

urlpatterns = [
    path('', lambda r: redirect('dashboard:index'), name='home'),
]
