import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0005_dailymetric_cost_breakdown'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FBAFeeRate',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_from', models.DateField(
                    help_text='First day this rate applies (inclusive). Stays in effect '
                              'until a later FBAFeeRate row for the same product takes over.',
                )),
                ('fba_fee_per_unit', models.DecimalField(
                    decimal_places=4, max_digits=10, help_text='USD per unit',
                )),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='fba_fee_rates',
                    to='dashboard.product',
                )),
                ('uploaded_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'ix_fba_fee_rates',
                'ordering': ['product', '-effective_from'],
                'unique_together': {('product', 'effective_from')},
                'indexes': [models.Index(fields=['product', 'effective_from'], name='ix_fba_fee_r_product_idx')],
            },
        ),
    ]
