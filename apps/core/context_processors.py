"""
apps/core/context_processors.py
"""
from django.conf import settings


def global_context(request):
    ctx = {
        'APP_VERSION': '2.0',
        'MARKETPLACES': list(settings.AMAZON_MARKETPLACES.keys()),
    }
    if request.user.is_authenticated and request.user.role:
        ctx['user_role'] = request.user.role
    return ctx
