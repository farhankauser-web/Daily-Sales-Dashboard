from django.db import migrations, models


# The canonical mapping, copied VERBATIM from
# apps/amazon_api/views.py::_CAMP_PREFIX_GROUP at the time of centralisation.
# Seeded with marketplace='' (global), which is exactly how it behaves today.
# Any edit to behaviour belongs in the Prefix Mapping page, not here.
SEED = [
    # ── US campaign naming ──────────────────────────────────────────────────
    ('8BTH',    'Bath Towels',         '8-Pack'),
    ('4BTH',    'Bath Towels',         '4-Pack'),
    ('2BTH',    'Bath Towels',         '2-Pack'),
    ('2BS',     'Bath Sheet',          '2-Pack'),
    ('1BS',     'Bath Sheet',          '1-Pack'),
    ('2BM',     'Bath Mat',            '2-Pack'),
    ('6HNDTWL', 'Hand Towel',          '6-Pack'),
    ('6KTH',    'Kitchen Towel',       '6-Pack'),
    ('3KTH',    'Kitchen Towel',       '3-Pack'),
    ('12KTH',   'Kitchen Towel',       '12-Pack'),
    ('12WCPK',  'Wash Cloth',          '12-Pack'),
    ('4WCPK',   'Wash Cloth',          '4-Pack'),
    ('4DT',     'Dish Towel',          '4-Pack'),
    # ── UK / EU / ME campaign naming (UK, UAE, KSA, DE) ─────────────────────
    ('BHTSHT',  'Bath Sheet',          '2-Pack'),
    ('BTHSHT',  'Bath Sheet',          '2-Pack'),
    ('BTMT',    'Bath Mat',            '2-Pack'),
    ('HND',     'Hand Towel',          '6-Pack'),
    ('HNDTWL',  'Hand Towel',          '6-Pack'),
    ('KTH',     'Kitchen Towel',       '6-Pack'),
    ('LUXPK2',  'Bath Towels',         '2-Pack'),
    ('LUX',     'Bath Towels',         '4-Pack'),
    ('PK2',     'Bath Towels',         '2-Pack'),
    ('PK4',     'Bath Towels',         '4-Pack'),
    ('PK8',     'Bath Towels',         '8-Pack'),
    ('WSH',     'Wash Cloth',          '12-Pack'),
    ('FTDDBL',  'Mattress Protector',  'Double'),
    ('FTDKNG',  'Mattress Protector',  'King'),
    ('FTDSKG',  'Mattress Protector',  'Super King'),
    ('MP',      'Mattress Protector',  'Double'),
]

NOTES = {
    'KTH': 'Default pack — campaign name omits it.',
    'LUX': 'Default pack for non-PK2 LUX.',
    'WSH': 'Default pack — campaign name omits it.',
    'MP':  'Default size — MP- omits it.',
}


def seed(apps, schema_editor):
    M = apps.get_model('dashboard', 'CampaignPrefixMap')
    for prefix, product_type, pack in SEED:
        M.objects.update_or_create(
            prefix=prefix, marketplace='',
            defaults={'product_type': product_type, 'pack': pack,
                      'active': True, 'note': NOTES.get(prefix, '')},
        )


def unseed(apps, schema_editor):
    M = apps.get_model('dashboard', 'CampaignPrefixMap')
    M.objects.filter(prefix__in=[p for p, _t, _k in SEED],
                     marketplace='').delete()


class Migration(migrations.Migration):

    dependencies = [('dashboard', '0038_adactionrequest')]

    operations = [
        migrations.CreateModel(
            name='CampaignPrefixMap',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('prefix', models.CharField(
                    help_text='Campaign-name prefix, upper-case, e.g. 4BTH.',
                    max_length=32)),
                ('product_type', models.CharField(
                    help_text="Product as it appears in Product.title's first "
                              "segment, e.g. 'Bath Towels'.", max_length=64)),
                ('pack', models.CharField(
                    help_text="Pack/size as it appears in Product.title's "
                              "second segment, e.g. '4-Pack'.", max_length=32)),
                ('marketplace', models.CharField(
                    blank=True, default='',
                    help_text='Blank = applies to every marketplace (current '
                              'behaviour). Reserved for future overrides.',
                    max_length=8)),
                ('active', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, max_length=256)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ix_campaign_prefix_map',
                'ordering': ['product_type', 'pack', 'prefix'],
            },
        ),
        migrations.AddIndex(
            model_name='campaignprefixmap',
            index=models.Index(fields=['active', 'prefix'],
                               name='ix_cpm_active_prefix_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='campaignprefixmap',
            unique_together={('prefix', 'marketplace')},
        ),
        migrations.RunPython(seed, unseed),
    ]
