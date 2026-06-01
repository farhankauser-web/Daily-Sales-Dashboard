import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqp', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── AIInsightCache ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='AIInsightCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('hash_key', models.CharField(
                    max_length=64, unique=True,
                    help_text='SHA-256 of canonical context JSON',
                )),
                ('insight_type', models.CharField(max_length=32, choices=[
                    ('asin_analysis', 'ASIN Analysis'),
                    ('keyword_analysis', 'Keyword Opportunity'),
                    ('executive_summary', 'Executive Summary'),
                    ('ai_chat', 'AI Chat'),
                ])),
                ('marketplace', models.CharField(blank=True, max_length=8)),
                ('asin', models.CharField(blank=True, max_length=16)),
                ('period_label', models.CharField(
                    blank=True, max_length=32,
                    help_text="Human label e.g. 'WoW 2026-W19 vs 2026-W18'",
                )),
                ('model_name', models.CharField(max_length=64)),
                ('prompt_tokens', models.IntegerField(default=0)),
                ('response_tokens', models.IntegerField(default=0)),
                ('latency_ms', models.IntegerField(default=0)),
                ('context_json', models.JSONField(help_text='Compressed context sent to Claude')),
                ('response_json', models.JSONField(help_text='Parsed JSON returned by Claude')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('hit_count', models.IntegerField(default=0,
                                                  help_text='Times this cache row has been served')),
                ('last_hit_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'ix_sqp_ai_cache',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='aiinsightcache',
            index=models.Index(fields=['insight_type', 'marketplace', 'asin'],
                               name='ix_sqp_ai_c_type_idx'),
        ),
        migrations.AddIndex(
            model_name='aiinsightcache',
            index=models.Index(fields=['-created_at'], name='ix_sqp_ai_c_created_idx'),
        ),

        # ── AIInsightHistory ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='AIInsightHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('insight_type', models.CharField(max_length=32, choices=[
                    ('asin_analysis', 'ASIN Analysis'),
                    ('keyword_analysis', 'Keyword Opportunity'),
                    ('executive_summary', 'Executive Summary'),
                    ('ai_chat', 'AI Chat'),
                ])),
                ('marketplace', models.CharField(blank=True, max_length=8)),
                ('asin', models.CharField(blank=True, max_length=16)),
                ('period_label', models.CharField(blank=True, max_length=32)),
                ('cache_hit', models.BooleanField(default=False)),
                ('request_payload', models.JSONField(blank=True, default=dict)),
                ('response_payload', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cache', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='history', to='sqp.aiinsightcache',
                )),
                ('user', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'ix_sqp_ai_history',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='aiinsighthistory',
            index=models.Index(fields=['-created_at'], name='ix_sqp_ai_h_created_idx'),
        ),
        migrations.AddIndex(
            model_name='aiinsighthistory',
            index=models.Index(fields=['user', '-created_at'], name='ix_sqp_ai_h_user_idx'),
        ),
        migrations.AddIndex(
            model_name='aiinsighthistory',
            index=models.Index(fields=['insight_type', '-created_at'], name='ix_sqp_ai_h_type_idx'),
        ),
    ]
