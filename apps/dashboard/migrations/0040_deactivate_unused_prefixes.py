"""
Deactivate campaign prefixes that no campaign actually uses.

Seeding brought across all 29 historical prefixes verbatim. Several of them are
aliases for a product that already has a working prefix, and some of those
aliases have never matched a single campaign in any marketplace — they are dead
configuration that makes the Prefix Mapping page harder to read.

SAFETY: the candidate list below is NOT applied blindly. Each candidate is
checked against THIS database's own PPCCampaignSnapshot rows, and is left
active if any campaign name starts with it. A prefix that is unused on one
environment but used on another therefore survives where it matters. Nothing is
deleted — `active=False` only removes it from the resolver going forward, and
the reverse migration restores it.
"""
from django.db import migrations

# Aliases observed to match no campaign. Verified per-database below.
CANDIDATES = [
    'BTMT',      # Bath Mat 2-Pack        — 2BM is the live prefix
    'BHTSHT',    # Bath Sheet 2-Pack      — 2BS is the live prefix
    'BTHSHT',    # Bath Sheet 2-Pack      — 2BS is the live prefix
    'LUXPK2',    # Bath Towels 2-Pack     — 2BTH is the live prefix
    'PK2',       # Bath Towels 2-Pack     — 2BTH is the live prefix
    'HNDTWL',    # Hand Towel 6-Pack      — 6HNDTWL / HND are live
    'FTDDBL',    # Mattress Protector Dbl — MP is the live prefix
]


def deactivate_unused(apps, schema_editor):
    CampaignPrefixMap = apps.get_model('dashboard', 'CampaignPrefixMap')
    PPCCampaignSnapshot = apps.get_model('dashboard', 'PPCCampaignSnapshot')

    names = set()
    for n in (PPCCampaignSnapshot.objects.order_by()
              .values_list('campaign_name', flat=True).distinct()):
        if n:
            names.add(n.upper().replace(' ', '').lstrip('-'))

    for prefix in CANDIDATES:
        used = any(n.startswith(prefix.upper()) for n in names)
        if used:
            continue          # live on this database — leave it alone
        (CampaignPrefixMap.objects
         .filter(prefix=prefix, active=True)
         .update(active=False,
                 note='Deactivated — no campaign uses this prefix. '
                      'Reactivate on the Prefix Mapping page if a campaign '
                      'is ever named with it.'))


def reactivate(apps, schema_editor):
    CampaignPrefixMap = apps.get_model('dashboard', 'CampaignPrefixMap')
    CampaignPrefixMap.objects.filter(prefix__in=CANDIDATES).update(
        active=True, note='')


class Migration(migrations.Migration):

    dependencies = [('dashboard', '0039_campaignprefixmap')]

    operations = [migrations.RunPython(deactivate_unused, reactivate)]
