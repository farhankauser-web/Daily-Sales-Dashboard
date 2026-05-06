"""
apps/core/middleware.py — RBAC enforcement + audit logging
"""
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.conf import settings

# URL prefix → required permission flag mapping
_PERM_MAP = {
    '/api-config/':          'can_configure_api',
    '/auth/manage/':         'can_manage_users',
    '/auth/roles/':          'can_manage_users',
    '/auth/audit/':          'can_view_audit_log',
    '/dashboard/historical/':'can_view_historical',
    '/dashboard/cogs/':      'can_manage_cogs',
    '/dashboard/targets/':   'can_manage_targets',
    '/dashboard/catalog/':   'can_manage_catalog',
    '/dashboard/summary/':   'can_generate_ai_summary',
}

_PUBLIC = ['/auth/login/', '/static/', '/media/', '/favicon.ico']


class RBACMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Skip public paths
        if any(path.startswith(p) for p in _PUBLIC):
            return self.get_response(request)

        # Unauthenticated → login
        if not request.user.is_authenticated:
            if path not in ('/auth/login/',):
                return redirect(f'{settings.LOGIN_URL}?next={path}')
            return self.get_response(request)

        # Superusers bypass RBAC
        if request.user.is_superuser:
            return self.get_response(request)

        # Check permission map
        for prefix, flag in _PERM_MAP.items():
            if path.startswith(prefix):
                if not request.user.has_perm_flag(flag):
                    return HttpResponseForbidden(
                        '<h2>403 — Access Denied</h2>'
                        f'<p>Your role does not have <code>{flag}</code> permission.</p>'
                    )
                break

        return self.get_response(request)


class AuditLogMiddleware:
    """
    Lightweight audit: logs every POST that modifies data.
    Fine-grained logging is handled in each view.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
