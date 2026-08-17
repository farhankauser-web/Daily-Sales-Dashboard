"""
normalize_product_titles — bring AE/KSA product titles onto the US/UK convention.

WHY THIS MATTERS
    Pulse derives a product's group from `Product.title` split on " - ":
    "Bath Towels - 4-Pack - Navy" → ("Bath Towels", "4-Pack"). The allocator
    uses that group to find a campaign's ASINs, and the Prefix Mapping page
    uses it to list a prefix's SKUs.

    US/UK products follow that convention. AE/KSA were loaded with a different
    one — "Bath Towel Pack 4 - pack 4 - Navy Blue" → ("Bath Towel Pack 4",
    "pack 4") — which is a group nothing maps to. Those SKUs are therefore
    invisible to group-based SB/SD allocation and clutter the Prefix Mapping
    page with phantom products.

WHAT IT CHANGES
    Only `Product.title`, and only the COLOUR-preserving first two segments,
    for the explicit list of families below. Nothing else on the product is
    touched; no campaign, SKU, ASIN, PPC or historical figure is modified.

WHAT IT DELIBERATELY SKIPS
    Titles where the PRODUCT NAME itself looks wrong rather than the format
    (e.g. a fitted-sheet SKU titled "Dish Towel - King"). Those need a human
    decision, so they are reported and left alone.

USAGE
    python manage.py normalize_product_titles              # dry run (default)
    python manage.py normalize_product_titles --apply
"""
from django.core.management.base import BaseCommand

from apps.dashboard.models import Product
from apps.dashboard.prefix_map import group_from_title

# (current product, current pack) -> (US/UK product, US/UK pack)
RENAMES = {
    ('Bath Towel 2',          'pack 2'):  ('Bath Towels',        '2-Pack'),
    ('Bath Towel Pack 4',     'pack 4'):  ('Bath Towels',        '4-Pack'),
    ('Bath Towel Pack 8',     'Pack 8'):  ('Bath Towels',        '8-Pack'),
    ('Bath Sheet pack 2',     'pack 2'):  ('Bath Sheet',         '2-Pack'),
    ('Bath Mat',              'pack 2'):  ('Bath Mat',           '2-Pack'),
    ('Hand Towel Pack 6',     'pack 6'):  ('Hand Towel',         '6-Pack'),
    ('Kitchen Towel pack 12', 'pack 12'): ('Kitchen Towel',      '12-Pack'),
    ('Kitchen Towel pack 6',  'pack 6'):  ('Kitchen Towel',      '6-Pack'),
    ('Kitchen Towel pack 3',  'pack TW'): ('Kitchen Towel',      '3-Pack'),
    ('Wash Cloth pack 12',    'pack 12'): ('Wash Cloth',         '12-Pack'),
    ('Wash Cloth pack 4',     'pack 4'):  ('Wash Cloth',         '4-Pack'),
    ('Dish Towel',            'pack 4'):  ('Dish Towel',         '4-Pack'),
    ('Mattress Protector',    'pack KNG'):('Mattress Protector', 'King'),
}

# Groups whose PRODUCT NAME is questionable, not just its formatting. Reported
# for a human to decide; never rewritten by this command.
REVIEW = {
    ('Dish Towel',    'King'):   'Fitted-sheet SKUs (FTD-*) titled as a dish towel.',
    ('Turkish Towel', 'King'):   'Fitted-sheet SKUs (FTD-*) titled as a Turkish towel.',
    ('Turkish Towel', 'pack 6'): 'Same SKU is titled "Dish Towel" in the other marketplace.',
    ('Dish Towel',    'pack 6'): 'Same SKU is titled "Turkish Towel" in the other marketplace.',
}


class Command(BaseCommand):
    help = ('Normalise AE/KSA product titles to the US/UK convention '
            '(dry run unless --apply).')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes. Without it, only reports.')
        parser.add_argument('--marketplace', default='',
                            help='Limit to one marketplace (default: all).')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        qs = Product.objects.all()
        if opts['marketplace']:
            qs = qs.filter(marketplace=opts['marketplace'])

        planned, review, unchanged = [], [], 0
        for p in qs.only('id', 'sku', 'title', 'marketplace'):
            parts = [x.strip() for x in (p.title or '').split(' - ') if x.strip()]
            if len(parts) < 2:
                unchanged += 1
                continue
            key = (parts[0], parts[1])
            if key in REVIEW:
                review.append((p, REVIEW[key]))
                continue
            target = RENAMES.get(key)
            if not target:
                unchanged += 1
                continue
            rest = parts[2:]
            new_title = ' - '.join([target[0], target[1]] + rest)
            if new_title != p.title:
                planned.append((p, new_title))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{len(planned)} product titles to normalise · '
            f'{len(review)} need review · {unchanged} already fine\n'))

        by_group = {}
        for p, new in planned:
            old_g = group_from_title(p.title)
            by_group.setdefault((old_g, group_from_title(new)), []).append((p, new))
        for (old_g, new_g), items in sorted(by_group.items(), key=lambda x: -len(x[1])):
            self.stdout.write(
                f'  {old_g[0]} · {old_g[1]}  →  '
                + self.style.SUCCESS(f'{new_g[0]} · {new_g[1]}')
                + f'   ({len(items)} products)')
            for p, new in items[:2]:
                self.stdout.write(f'      {p.marketplace} {p.sku:22} {p.title!r}')
                self.stdout.write(f'      {"":3} {"":22} → {new!r}')
            if len(items) > 2:
                self.stdout.write(f'      … and {len(items) - 2} more')

        if review:
            self.stdout.write(self.style.WARNING(
                '\n  NOT changed — the product name itself looks wrong, '
                'not just the format:'))
            seen = set()
            for p, why in review:
                g = group_from_title(p.title)
                if g in seen:
                    continue
                seen.add(g)
                n = sum(1 for q, _ in review if group_from_title(q.title) == g)
                self.stdout.write(f'    {g[0]} · {g[1]} ({n} products) — {why}')
                self.stdout.write(f'      e.g. {p.marketplace} {p.sku} {p.title!r}')

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f'\nDRY RUN — nothing written. Re-run with --apply to update '
                f'{len(planned)} titles.\n'))
            return

        for p, new in planned:
            p.title = new
        Product.objects.bulk_update([p for p, _ in planned], ['title'],
                                    batch_size=200)
        self.stdout.write(self.style.SUCCESS(
            f'\nUpdated {len(planned)} product titles.\n'))
