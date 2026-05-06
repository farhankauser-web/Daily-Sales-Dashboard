from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0003_productmonthlytarget'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductTypePackMonthlyTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('marketplace', models.CharField(max_length=8)),
                ('product_type', models.CharField(max_length=128)),
                ('pack_size', models.CharField(max_length=64)),
                ('month', models.DateField(help_text='First day of month')),
                ('revenue_target', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'ix_product_type_pack_monthly_targets',
                'ordering': ['month', 'marketplace', 'product_type', 'pack_size'],
                'unique_together': {('marketplace', 'product_type', 'pack_size', 'month')},
            },
        ),
    ]
