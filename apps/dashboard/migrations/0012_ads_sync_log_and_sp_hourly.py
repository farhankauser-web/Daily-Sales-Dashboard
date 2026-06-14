from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0011_dailymetric_finalized_at'),
    ]

    operations = [
        # ── AdsDataSyncLog ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='AdsDataSyncLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('date', models.DateField()),
                ('source', models.CharField(choices=[
                    ('sp_hourly', 'SP Hourly (Ads API timeUnit=HOURLY)'),
                    ('sb_daily',  'SB Daily Campaign Report'),
                    ('sd_daily',  'SD Daily Campaign Report'),
                    ('orders',    'SP-API Orders Hourly'),
                ], max_length=16)),
                ('status', models.CharField(choices=[
                    ('ok',                 'OK — rows received'),
                    ('empty_from_amazon',  'OK — Amazon returned 0 rows (treat as 0 spend)'),
                    ('failed',             'Failed — error during fetch / parse'),
                    ('pending',            'In-flight — report submitted, waiting for Amazon'),
                ], default='pending', max_length=20)),
                ('rows_received', models.IntegerField(default=0)),
                ('error_message', models.TextField(blank=True)),
                ('report_id', models.CharField(
                    blank=True, max_length=64,
                    help_text='Amazon Ads API report_id when applicable',
                )),
                ('last_synced', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'ix_ads_data_sync_log',
                'ordering': ['-date', 'marketplace', 'source'],
                'unique_together': {('marketplace', 'date', 'source')},
            },
        ),
        migrations.AddIndex(
            model_name='adsdatasynclog',
            index=models.Index(fields=['marketplace', '-date'], name='ix_adsd_mp_date_idx'),
        ),
        migrations.AddIndex(
            model_name='adsdatasynclog',
            index=models.Index(fields=['marketplace', 'date', 'source'],
                               name='ix_adsd_mp_d_s_idx'),
        ),
        migrations.AddIndex(
            model_name='adsdatasynclog',
            index=models.Index(fields=['status', '-last_synced'], name='ix_adsd_status_idx'),
        ),

        # ── PPCCampaignHourlySnapshot ────────────────────────────────────────
        migrations.CreateModel(
            name='PPCCampaignHourlySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('date', models.DateField(help_text='Date in marketplace local TZ')),
                ('hour', models.PositiveSmallIntegerField(
                    help_text='0-23, marketplace local TZ')),
                ('campaign_id', models.CharField(max_length=64)),
                ('campaign_name', models.CharField(blank=True, max_length=256)),
                ('campaign_type', models.CharField(
                    default='sp', max_length=4,
                    help_text="Always 'sp' — SB/SD don't have hourly",
                )),
                ('spend', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('impressions', models.BigIntegerField(default=0)),
                ('clicks', models.BigIntegerField(default=0)),
                ('orders_7d', models.IntegerField(default=0)),
                ('sales_7d', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('units_7d', models.IntegerField(default=0)),
                ('synced_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ix_ppc_campaign_hourly_snapshot',
                'ordering': ['-date', 'hour', 'campaign_id'],
                'unique_together': {('marketplace', 'date', 'hour', 'campaign_id')},
            },
        ),
        migrations.AddIndex(
            model_name='ppccampaignhourlysnapshot',
            index=models.Index(fields=['marketplace', '-date', 'hour'],
                               name='ix_ppch_mp_d_h_idx'),
        ),
        migrations.AddIndex(
            model_name='ppccampaignhourlysnapshot',
            index=models.Index(fields=['marketplace', 'campaign_id', '-date'],
                               name='ix_ppch_mp_camp_d_idx'),
        ),
        migrations.AddIndex(
            model_name='ppccampaignhourlysnapshot',
            index=models.Index(fields=['-date', 'hour'], name='ix_ppch_date_hour_idx'),
        ),
    ]
