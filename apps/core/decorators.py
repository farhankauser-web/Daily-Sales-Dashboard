"""
apps/core/decorators.py
"""
from functools import wraps
from django.http import HttpResponseForbidden


def permission_required(flag: str):
    """
    View decorator: checks request.user.has_perm_flag(flag).
    Usage:
        @permission_required('can_configure_api')
        def my_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser or request.user.has_perm_flag(flag):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden(
                '<h2>403 — Insufficient Permissions</h2>'
                f'<p>Required: <code>{flag}</code></p>'
            )
        return _wrapped
    return decorator
