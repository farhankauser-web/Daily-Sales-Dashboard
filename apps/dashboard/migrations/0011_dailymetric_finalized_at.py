from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0010_rename_ix_hms_mp_d_h_idx_ix_hourly_m_marketp_41d3f7_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailymetric',
            name='finalized_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text=(
                    "Set by the 00:45 finalize_yesterday cron once the day's "
                    "order data is locked. Hourly cron skips finalized rows. "
                    "PPC fields may still update via backfill_ppc for 7 days."
                ),
            ),
        ),
        migrations.AddField(
            model_name='dailyskusnapshot',
            name='finalized_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text=(
                    "Set by finalize_yesterday cron — locks per-SKU row from further writes."
                ),
            ),
        ),
    ]
