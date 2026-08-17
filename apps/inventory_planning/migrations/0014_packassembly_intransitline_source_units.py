from django.db import migrations, models


# INV-REPACK-001: base(procurement) SKU -> assembled(retail) SKU, ratio 2:1.
# Left = base/component (what the PO & opening balance are booked as),
# right = assembled (what ships to and sells on Amazon).
PAIRS = [
    # Kitchen towels: pack of 6 -> pack of 12
    ('TW-BLK-KTH-6', 'TW-BLK-KTH-12'),
    ('TW-GRY-KTH-6', 'TW-GRY-KTH-12'),
    ('TW-GRN-KTH-6', 'TW-GRN-KTH-12'),
    ('TW-BLU-KTH-6', 'TW-BLU-KTH-12'),
    ('TW-YEL-KTH-6', 'TW-YEL-KTH-12'),
    ('TW-RED-KTH-6', 'TW-RED-KTH-12'),
    # Wash cloths: pack of 12 -> pack of 24
    ('WSH-CLT-NBL-12', 'WSH-CLT-24-NBL'),
    ('WSH-CLT-DGY-12', 'WSH-CLT-24-DGY'),
    ('WSH-CLT-WHT-12', 'WSH-CLT-24-WHT'),
    ('WSH-CLT-SND-12', 'WSH-CLT-24-SND'),
    ('WSH-CLT-LGY-12', 'WSH-CLT-24-LGY'),
    ('WSH-CLT-TEL-12', 'WSH-CLT-24-TEL'),
    ('WSH-CLT-PUR-12', 'WSH-CLT-24-PUR'),
    ('WSH-CLT-BLU-12', 'WSH-CLT-24-BLU'),
    ('WSH-CLT-RED-12', 'WSH-CLT-24-RED'),
]


def seed(apps, schema_editor):
    PackAssembly = apps.get_model('inventory_planning', 'PackAssembly')
    for base, assembled in PAIRS:
        PackAssembly.objects.update_or_create(
            assembled_sku=assembled,
            defaults={'component_sku': base, 'component_per_pack': 2,
                      'active': True},
        )


def unseed(apps, schema_editor):
    PackAssembly = apps.get_model('inventory_planning', 'PackAssembly')
    PackAssembly.objects.filter(
        assembled_sku__in=[a for _, a in PAIRS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory_planning', '0013_intransitline_opening_balance_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='intransitline',
            name='source_units',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='PackAssembly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('assembled_sku', models.CharField(max_length=64, unique=True)),
                ('component_sku', models.CharField(max_length=64)),
                ('component_per_pack', models.PositiveIntegerField(default=2)),
                ('active', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, max_length=128)),
            ],
            options={
                'ordering': ['assembled_sku'],
            },
        ),
        migrations.AddIndex(
            model_name='packassembly',
            index=models.Index(fields=['assembled_sku'],
                               name='inv_pack_asm_sku_idx'),
        ),
        migrations.RunPython(seed, unseed),
    ]
