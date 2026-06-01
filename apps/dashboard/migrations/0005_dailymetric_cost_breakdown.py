from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0004_producttypepackmonthlytarget'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailymetric',
            name='cgs',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=14,
                help_text='Sum of COGS unit_cost × qty across all orders this day',
            ),
        ),
        migrations.AddField(
            model_name='dailymetric',
            name='amazon_fee',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=14,
                help_text='Amazon referral fee — typically revenue × 15%',
            ),
        ),
        migrations.AddField(
            model_name='dailymetric',
            name='fba_fee',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=14,
                help_text='Amazon FBA fulfilment fee — from COGS shipping_cost × qty',
            ),
        ),
    ]
