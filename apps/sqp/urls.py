from django.urls import path

from . import views

app_name = 'sqp'

urlpatterns = [
    path('',                       views.overview,         name='overview'),

    # JSON API
    path('api/overview/',          views.api_overview,     name='api_overview'),
    path('api/queries/',           views.api_queries,      name='api_queries'),
    path('api/trends/',            views.api_trends,       name='api_trends'),

    # On-demand sync (used by the "↻ Refresh latest week" button)
    path('sync/',                  views.sync_latest_week, name='sync'),

    # AI Insights — Phase A: ASIN analyzer
    path('api/ai/asin/',           views.api_ai_asin_analysis, name='api_ai_asin'),
]
