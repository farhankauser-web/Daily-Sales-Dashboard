from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('dashboard', '0037_campaignbudgetusagedaily'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdActionRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('action_id', models.CharField(
                    help_text='Stable id — the idempotency key for execution.',
                    max_length=40, unique=True)),
                ('marketplace', models.CharField(max_length=8)),
                ('entity_type', models.CharField(
                    choices=[('campaign', 'Campaign')], default='campaign',
                    max_length=16)),
                ('entity_id', models.CharField(max_length=64)),
                ('entity_name', models.CharField(blank=True, max_length=256)),
                ('action_type', models.CharField(
                    choices=[('campaign_budget', 'Campaign daily budget')],
                    max_length=24)),
                ('opportunity_key', models.CharField(blank=True, max_length=128)),
                ('reason', models.TextField(blank=True)),
                ('evidence', models.JSONField(
                    blank=True, default=list,
                    help_text='The numbers cited when this was proposed.')),
                ('confidence', models.CharField(blank=True, max_length=16)),
                ('from_sku', models.CharField(
                    blank=True,
                    help_text='The SKU investigation this came from, if any.',
                    max_length=64)),
                ('current_value', models.DecimalField(
                    decimal_places=2,
                    help_text='Value observed when the action was proposed.',
                    max_digits=12)),
                ('proposed_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('value_before', models.DecimalField(
                    blank=True, decimal_places=2,
                    help_text='Value read back immediately before executing.',
                    max_digits=12, null=True)),
                ('value_after', models.DecimalField(
                    blank=True, decimal_places=2, max_digits=12, null=True)),
                ('data_period_start', models.DateField(blank=True, null=True)),
                ('data_period_end', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[('proposed', 'Proposed — awaiting review'),
                             ('approved', 'Approved — cleared to execute'),
                             ('executing', 'Executing'),
                             ('executed', 'Executed'),
                             ('failed', 'Failed'),
                             ('rejected', 'Rejected'),
                             ('cancelled', 'Cancelled'),
                             ('stale', 'Stale — underlying value moved, re-review required'),
                             ('unavailable', 'Execution unavailable — integration is read-only')],
                    db_index=True, default='proposed', max_length=12)),
                ('proposed_at', models.DateTimeField(auto_now_add=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('executed_at', models.DateTimeField(blank=True, null=True)),
                ('amazon_status', models.CharField(blank=True, max_length=24)),
                ('amazon_response', models.TextField(blank=True)),
                ('failure_reason', models.TextField(blank=True)),
                ('dry_run', models.BooleanField(
                    default=False,
                    help_text='True when validated through the pipeline without '
                              'contacting Amazon.')),
                ('note', models.TextField(blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ad_actions_approved',
                    to=settings.AUTH_USER_MODEL)),
                ('proposed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ad_actions_proposed',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'ix_ad_action_request',
                'ordering': ['-proposed_at'],
            },
        ),
        migrations.AddIndex(
            model_name='adactionrequest',
            index=models.Index(fields=['marketplace', 'status', '-proposed_at'],
                               name='ix_adact_mp_status_idx'),
        ),
        migrations.AddIndex(
            model_name='adactionrequest',
            index=models.Index(fields=['entity_type', 'entity_id', '-proposed_at'],
                               name='ix_adact_entity_idx'),
        ),
    ]
