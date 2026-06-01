"""
Infinitee Xclusives — URL Configuration
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/',      include('apps.users.urls',       namespace='users')),
    path('dashboard/', include('apps.dashboard.urls',   namespace='dashboard')),
    path('sqp/',       include('apps.sqp.urls',         namespace='sqp')),
    path('api-config/',include('apps.amazon_api.urls',  namespace='amazon_api')),
    path('',           include('apps.core.urls',        namespace='core')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom admin site branding
admin.site.site_header = "Infinitee Xclusives Admin"
admin.site.site_title  = "Infinitee Admin"
admin.site.index_title = "Operations Control Panel"
