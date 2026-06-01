"""
cron_setup.py — Scheduled-job reference for Infinitee data sync.

The Historical view reads from the DailyMetric table — never live from SP-API.
A daily cron pulls yesterday's FlatFileAllOrdersReport and writes one row per
(marketplace, day). Today's partial-day row is refreshed only when the user
clicks "Refresh today" on the dashboard.

Add this to your server's crontab (`crontab -e`) — adjust the absolute paths
to your Python virtualenv and project root:

    PYTHON=/path/to/venv/bin/python
    MANAGE=/path/to/infinitee_app/manage.py

    # ── Historical sync ─────────────────────────────────────────────────────
    # Pulls yesterday's FlatFileAllOrdersReport for every active marketplace
    # and writes per-day rows into DailyMetric.
    # 10:00 UTC == 03:00 PT (US), 11:00 GMT (UK), 13:00 CET (DE), 14:00 GST (UAE)
    0 10 * * * $PYTHON $MANAGE sync_daily_metrics  >> /tmp/ix_sync.log 2>&1

    # Safety re-run 6 hours later in case Amazon's report was slow
    0 16 * * * $PYTHON $MANAGE sync_daily_metrics  >> /tmp/ix_sync.log 2>&1

    # ── One-time backfill (run manually after setup, NOT in crontab) ────────
    # python manage.py backfill_history --days 30
    # python manage.py backfill_history --start 2026-01-01 --end 2026-05-10

    # ── Optional: recompute COGS-based margins after a COGS upload ──────────
    # 5 2 * * * $PYTHON $MANAGE calculate_margins  >> /tmp/ix_margins.log 2>&1


Initial setup checklist
-----------------------
1. Verify SP-API config is active for each marketplace:
       python manage.py shell -c "from apps.amazon_api.models import AmazonAPIConfig; \\
           print(list(AmazonAPIConfig.objects.filter(is_active=True).values_list('marketplace', flat=True)))"

2. Backfill the last 30 days (one bulk report per marketplace, ~1-3 min each):
       python manage.py backfill_history --days 30

3. Add the two cron lines above to your server crontab.

4. (Optional) Confirm the daily job works by running it manually:
       python manage.py sync_daily_metrics --date $(date -v-1d +%Y-%m-%d)


Alternative schedulers
----------------------
- django-crontab:   pip install django-crontab; CRONJOBS = [...]
- Celery Beat:      tasks defined in apps/dashboard/tasks.py
- systemd timer:    /etc/systemd/system/ix-sync.timer
"""

if __name__ == '__main__':
    print(__doc__)
