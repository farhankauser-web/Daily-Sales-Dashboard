"""
Seed the Search Intelligence Center's ProductGroup table from the catalog.

Reports by default and only writes with --apply, per the repo convention for
anything that mutates state.

THE CATALOG IS THE DEFINITION. A group is a set of `Product.category` values;
the advertising that belongs to it is derived from which ad groups advertised
those ASINs (see `apps.dashboard.sti.mapping`). Campaign initials are recorded
alongside for READING reports and are never used to scope one — a group whose
categories are wrong shows the wrong products, and that is the only thing worth
curating.
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils.text import slugify

from apps.dashboard.models import (
    AdsAdvertisedProductDailySnapshot, Campaign, Product, ProductGroup,
)

# name → (category prefixes, descriptive campaign initials).
# Categories define the group. Initials are a reading aid only.
SEED = [
    ('Bath Towels',        ['Bath Towel', 'Turkish Towel'], ['BTH', 'BT', 'BHT', 'LUX']),
    ('Bath Sheets',        ['Bath Sheet'],                  ['BS']),
    ('Hand Towels',        ['Hand Towel'],                  ['HNDTWL', 'HND']),
    ('Wash Cloths',        ['Wash Cloth'],                  ['WCPK', 'WSH']),
    ('Kitchen Towels',     ['Kitchen Towel', 'Dish Towel'], ['KTH', 'DT']),
    ('Bath Mats',          ['Bath Mat'],                    ['BM']),
    ('Bed Linen',          ['Bedsheet', 'Fitted Bedsheet', 'Microfiber Bedsheet',
                            'Duvet', 'Pillow Case', 'Blanket'],  ['FTD']),
    ('Mattress Protectors', ['Mattress Protector'],         ['MP']),
]

MARKETPLACES = ['usa', 'uk', 'ae', 'sa']


class Command(BaseCommand):
    help = 'Seed ProductGroup rows from the product catalog (dry run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the rows. Without this the command only reports.')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']

        all_categories = [
            c for c in Product.objects.order_by()
            .values_list('category', flat=True).distinct() if c
        ]
        known_initials = set(
            Campaign.objects.exclude(initials='').order_by()
            .values_list('initials', flat=True).distinct()
        )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{len(all_categories)} catalog categories across '
            f'{Product.objects.count()} products'))

        used_categories = set()
        created = updated = 0

        for name, prefixes, initials in SEED:
            categories = sorted(
                c for c in all_categories
                if any(c.lower().startswith(p.lower()) for p in prefixes)
            )
            if not categories:
                self.stdout.write(f'  skip  {name} — no catalog categories matched')
                continue
            used_categories.update(categories)

            # Per-marketplace ASIN counts: the number that decides whether a
            # group can report at all, now that scoping is catalog-driven.
            counts = []
            for mp in MARKETPLACES:
                n = Product.objects.filter(marketplace=mp, category__in=categories).count()
                if n:
                    counts.append(f'{mp}:{n}')

            self.stdout.write(
                f'  {name:22} categories={len(categories):2}  '
                f'ASINs {" ".join(counts) or "none"}')

            if apply_changes:
                _, was_created = ProductGroup.objects.update_or_create(
                    slug=slugify(name),
                    defaults={'name': name, 'categories': categories,
                              'initials': sorted(i for i in initials if i in known_initials),
                              'lexicon_key': 'towel', 'active': True},
                )
                created += int(was_created)
                updated += int(not was_created)

        orphan_categories = sorted(set(all_categories) - used_categories)
        if orphan_categories:
            self.stdout.write(self.style.WARNING(
                f'\n  Catalog categories in no group: {", ".join(orphan_categories)}'))

        # The measure that matters under catalog scoping: advertised ASINs the
        # catalog cannot place. Campaign naming no longer affects coverage.
        self.stdout.write('')
        for mp in MARKETPLACES:
            rows = (AdsAdvertisedProductDailySnapshot.objects
                    .filter(marketplace=mp).values('asin').annotate(sp=Sum('spend')))
            known = set(Product.objects.filter(marketplace=mp, category__in=used_categories)
                        .order_by().values_list('asin', flat=True).distinct())
            unplaced = [r for r in rows if r['asin'] not in known]
            spend = sum(float(r['sp'] or 0) for r in unplaced)
            total = sum(float(r['sp'] or 0) for r in rows)
            pct = (100 - spend / total * 100) if total else 0
            self.stdout.write(
                f'  {mp}: advertised ASINs placed by catalog = {pct:5.1f}% of spend'
                + (f'  ({len(unplaced)} unplaced)' if unplaced else ''))

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f'\nWrote {created} new / {updated} updated product groups.'))
        else:
            self.stdout.write(self.style.NOTICE(
                '\nDry run — nothing written. Re-run with --apply to persist.'))
