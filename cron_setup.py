"""
cron_setup.py — Example cron configuration for Infinitee data sync

Add to your server's crontab (crontab -e):
"""

CRON_JOBS = """
# ─────────────────────────────────────────────────────────────────────────────
# Infinitee Xclusives — Scheduled Jobs
# ─────────────────────────────────────────────────────────────────────────────
# Path to your virtualenv python — adjust as needed
PYTHON=/path/to/venv/bin/python
MANAGE=/path/to/infinitee_app/manage.py

# Sync SP-API data — runs every 3 hours (Amazon updates ~hourly)
0 */3 * * * $PYTHON $MANAGE sync_amazon_data --days 1 >> /tmp/ix_sync.log 2>&1

# Daily full sync at 2 AM (catches any missed windows)
0 2 * * * $PYTHON $MANAGE sync_amazon_data --days 2 >> /tmp/ix_sync.log 2>&1

# Recalculate margins after COGS sync (runs 5 min after sync)
5 2 * * * $PYTHON $MANAGE calculate_margins >> /tmp/ix_margins.log 2>&1

# Weekly: seed historical data for any gaps (Sunday 3 AM)
0 3 * * 0 $PYTHON $MANAGE sync_amazon_data --days 7 >> /tmp/ix_weekly.log 2>&1

# ─────────────────────────────────────────────────────────────────────────────
# Alternative: Use django-crontab or Celery Beat for production
# pip install django-crontab
# INSTALLED_APPS += ['django_crontab']
# CRONJOBS = [
#     ('0 */3 * * *', 'django.core.management.call_command', ['sync_amazon_data']),
# ]
# python manage.py crontab add
# ─────────────────────────────────────────────────────────────────────────────
"""

if __name__ == '__main__':
    print(CRON_JOBS)
