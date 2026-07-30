from django.contrib import admin
from .models import UserDashboardLayout


@admin.register(UserDashboardLayout)
class UserDashboardLayoutAdmin(admin.ModelAdmin):
    list_display = ('user', 'widget_count', 'updated_at')
    readonly_fields = ('updated_at',)

    @admin.display(description='widgets')
    def widget_count(self, obj):
        return len(obj.layout or [])
