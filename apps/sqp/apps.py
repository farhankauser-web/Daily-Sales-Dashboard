from django.apps import AppConfig


class SqpConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name              = 'apps.sqp'
    label             = 'sqp'
    verbose_name      = 'Search Query Performance'
