"""
apps/users/admin.py
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Role, AuditLog


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display  = ['name', 'slug', 'user_count', 'can_configure_api', 'can_manage_users']
    list_filter   = ['slug']
    search_fields = ['name']

    def user_count(self, obj):
        return obj.users.count()
    user_count.short_description = 'Users'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display   = ['email', 'full_name', 'role', 'is_active', 'login_count', 'last_login']
    list_filter    = ['is_active', 'is_staff', 'role']
    search_fields  = ['email', 'first_name', 'last_name']
    ordering       = ['email']
    fieldsets      = (
        (None,           {'fields': ('email', 'password')}),
        ('Personal',     {'fields': ('first_name', 'last_name', 'phone', 'timezone')}),
        ('Access',       {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'allowed_marketplaces')}),
        ('Stats',        {'fields': ('login_count', 'last_login_ip', 'created_at'), 'classes': ('collapse',)}),
    )
    add_fieldsets  = (
        (None, {'classes': ('wide',), 'fields': ('email', 'first_name', 'last_name', 'password1', 'password2', 'role')}),
    )
    readonly_fields = ['login_count', 'last_login_ip', 'created_at']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'user', 'action', 'resource', 'ip_address']
    list_filter   = ['action']
    search_fields = ['user__email', 'resource']
    readonly_fields = ['user', 'action', 'resource', 'detail', 'ip_address', 'user_agent', 'created_at']
    ordering      = ['-created_at']
