import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── SQPQuery ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='SQPQuery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('text', models.CharField(max_length=512, unique=True)),
                ('text_lower', models.CharField(db_index=True, max_length=512,
                                                help_text='Lowercased for case-insensitive search')),
                ('first_seen', models.DateField()),
                ('last_seen', models.DateField()),
                ('total_volume', models.BigIntegerField(default=0,
                                                        help_text='Running sum of search_query_volume across all snapshots')),
                ('snapshot_count', models.IntegerField(default=0,
                                                       help_text='How many SQPSnapshot rows reference this query')),
            ],
            options={
                'db_table': 'ix_sqp_queries',
                'ordering': ['-last_seen', 'text_lower'],
            },
        ),
        migrations.AddIndex(
            model_name='sqpquery',
            index=models.Index(fields=['-total_volume'], name='ix_sqp_q_total_vol_idx'),
        ),
        migrations.AddIndex(
            model_name='sqpquery',
            index=models.Index(fields=['-last_seen'], name='ix_sqp_q_last_seen_idx'),
        ),

        # ── SQPReport ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='SQPReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('asin', models.CharField(blank=True, default='', max_length=16,
                                          help_text='Empty = brand-level report')),
                ('period_type', models.CharField(choices=[
                    ('WEEK', 'Week'), ('MONTH', 'Month'), ('QUARTER', 'Quarter'),
                ], max_length=10)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('sp_report_id', models.CharField(blank=True, max_length=64,
                                                  help_text='SP-API reportId returned by createReport')),
                ('status', models.CharField(choices=[
                    ('pending', 'Pending'), ('in_progress', 'In Progress'),
                    ('done', 'Done'), ('failed', 'Failed'), ('empty', 'Empty (no rows)'),
                ], default='pending', max_length=16)),
                ('rows_loaded', models.IntegerField(default=0)),
                ('error_message', models.TextField(blank=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('triggered_by', models.ForeignKey(blank=True, null=True,
                                                   on_delete=django.db.models.deletion.SET_NULL,
                                                   to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'ix_sqp_reports',
                'ordering': ['-period_start', 'marketplace'],
                'unique_together': {('marketplace', 'asin', 'period_type', 'period_start')},
            },
        ),
        migrations.AddIndex(
            model_name='sqpreport',
            index=models.Index(fields=['marketplace', 'period_type', '-period_start'],
                               name='ix_sqp_r_mp_pt_ps_idx'),
        ),
        migrations.AddIndex(
            model_name='sqpreport',
            index=models.Index(fields=['status', '-requested_at'], name='ix_sqp_r_status_idx'),
        ),

        # ── SQPSnapshot ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name='SQPSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(max_length=8)),
                ('asin', models.CharField(blank=True, default='', max_length=16,
                                          help_text='Empty = brand-level row')),
                ('period_type', models.CharField(choices=[
                    ('WEEK', 'Week'), ('MONTH', 'Month'), ('QUARTER', 'Quarter'),
                ], max_length=10)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('search_query_score', models.IntegerField(default=0,
                                                           help_text='Rank within the period — 1 = top')),
                ('search_query_volume', models.BigIntegerField(default=0)),
                ('impressions_total', models.BigIntegerField(default=0)),
                ('impressions_asin_count', models.BigIntegerField(default=0)),
                ('impressions_asin_share', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('clicks_total', models.BigIntegerField(default=0)),
                ('clicks_asin_count', models.BigIntegerField(default=0)),
                ('clicks_asin_share', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('click_rate', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('clicks_median_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('atc_total', models.BigIntegerField(default=0)),
                ('atc_asin_count', models.BigIntegerField(default=0)),
                ('atc_asin_share', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('atc_rate', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('atc_median_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('purchases_total', models.BigIntegerField(default=0)),
                ('purchases_asin_count', models.BigIntegerField(default=0)),
                ('purchases_asin_share', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('purchase_rate', models.DecimalField(decimal_places=6, default=0, max_digits=8)),
                ('purchases_median_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('query', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='snapshots', to='sqp.sqpquery')),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name='snapshots', to='sqp.sqpreport')),
            ],
            options={
                'db_table': 'ix_sqp_snapshots',
                'ordering': ['-period_start', '-search_query_volume'],
                'unique_together': {('marketplace', 'asin', 'query', 'period_type', 'period_start')},
            },
        ),
        migrations.AddIndex(
            model_name='sqpsnapshot',
            index=models.Index(fields=['marketplace', 'period_type', '-period_start'],
                               name='ix_sqp_s_mp_pt_ps_idx'),
        ),
        migrations.AddIndex(
            model_name='sqpsnapshot',
            index=models.Index(fields=['marketplace', 'asin', 'period_type', '-period_start'],
                               name='ix_sqp_s_mp_asin_idx'),
        ),
        migrations.AddIndex(
            model_name='sqpsnapshot',
            index=models.Index(fields=['query', 'period_type', '-period_start'],
                               name='ix_sqp_s_query_idx'),
        ),
        migrations.AddIndex(
            model_name='sqpsnapshot',
            index=models.Index(fields=['period_type', 'period_start', '-search_query_volume'],
                               name='ix_sqp_s_vol_sort_idx'),
        ),
    ]
