from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0008_add_daily_sku_snapshot'),
    ]

    operations = [
        # ── HourlyMetricSnapshot ─────────────────────────────────────────────
        migrations.CreateModel(
            name='HourlyMetricSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('date', models.DateField(help_text='Date in marketplace local TZ')),
                ('hour', models.PositiveSmallIntegerField(help_text='0-23, marketplace local TZ')),
                ('revenue', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('units', models.IntegerField(default=0)),
                ('orders', models.IntegerField(default=0)),
                ('cgs', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('amazon_fee', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('fba_fee', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('gross_margin', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('gm_pct', models.DecimalField(decimal_places=4, default=0, max_digits=6)),
                ('contribution_margin', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('cm_pct', models.DecimalField(decimal_places=4, default=0, max_digits=6)),
                ('ppc_spend', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('synced_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ix_hourly_metric_snapshot',
                'ordering': ['-date', 'hour'],
                'unique_together': {('marketplace', 'date', 'hour')},
            },
        ),
        migrations.AddIndex(
            model_name='hourlymetricsnapshot',
            index=models.Index(fields=['marketplace', '-date', 'hour'], name='ix_hms_mp_d_h_idx'),
        ),
        migrations.AddIndex(
            model_name='hourlymetricsnapshot',
            index=models.Index(fields=['-date'], name='ix_hms_date_idx'),
        ),

        # ── HourlySkuSnapshot ────────────────────────────────────────────────
        migrations.CreateModel(
            name='HourlySkuSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('date', models.DateField()),
                ('hour', models.PositiveSmallIntegerField()),
                ('sku', models.CharField(max_length=64)),
                ('asin', models.CharField(blank=True, max_length=16)),
                ('qty', models.IntegerField(default=0)),
                ('revenue', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('cgs', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('amazon_fee', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('fba_fee', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('contribution_margin', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('synced_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ix_hourly_sku_snapshot',
                'ordering': ['-date', 'hour', 'sku'],
                'unique_together': {('marketplace', 'date', 'hour', 'sku')},
            },
        ),
        migrations.AddIndex(
            model_name='hourlyskusnapshot',
            index=models.Index(fields=['marketplace', 'date', 'hour'], name='ix_hss_mp_d_h_idx'),
        ),
        migrations.AddIndex(
            model_name='hourlyskusnapshot',
            index=models.Index(fields=['marketplace', 'sku', '-date'], name='ix_hss_mp_sku_d_idx'),
        ),
    ]
