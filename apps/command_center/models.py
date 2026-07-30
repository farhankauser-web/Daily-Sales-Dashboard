"""
Command Center — per-user, configurable widget dashboard.

One row per user holds the full layout as JSON (Gridstack-friendly):
    [{"key": "kpi_revenue", "x":0,"y":0,"w":3,"h":2, "config": {...}}, ...]
Keeping it as a single JSON blob (rather than a row per widget) matches how
Gridstack serialises/deserialises a grid, so save/restore is one round-trip.
"""
from django.conf import settings
from django.db import models


class UserDashboardLayout(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='command_center_layout')
    # list[dict]: {key, x, y, w, h, config}
    layout = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'dashboard layout'

    def __str__(self):
        return f'Command Center layout · {self.user} · {len(self.layout or [])} widgets'
