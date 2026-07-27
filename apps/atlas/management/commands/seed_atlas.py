"""Seed Atlas base data: companies (Infinitee, RMT) + the SOW payment terms."""
from django.core.management.base import BaseCommand

from apps.atlas.models import AtlasCompany, PaymentTerm

TERMS = [
    ('Cash', 0, 0), ('30 days', 30, 0), ('45 days', 45, 0),
    ('60 days', 60, 0), ('90 days', 90, 0), ('120 days', 120, 0),
    ('30% advance + 60 days', 60, 30),
    ('30% advance + 90 days', 90, 30),
    ('30% advance + 120 days', 120, 30),
]

COMPANIES = [
    ('infinitee', 'Infinitee Xclusives', 'USD', '0.00'),
    ('rmt',       'Rushmore Trading L.L.C.', 'AED', '0.05'),
]


class Command(BaseCommand):
    help = 'Seed Atlas companies and payment terms (idempotent).'

    def handle(self, **_):
        for code, name, ccy, vat in COMPANIES:
            _, created = AtlasCompany.objects.get_or_create(
                code=code, defaults={'name': name, 'currency': ccy,
                                     'vat_rate': vat})
            self.stdout.write(f'company {code}: {"created" if created else "exists"}')
        for i, (name, days, adv) in enumerate(TERMS):
            _, created = PaymentTerm.objects.get_or_create(
                name=name, defaults={'days': days, 'advance_pct': adv,
                                     'sort_order': i})
            self.stdout.write(f'term {name}: {"created" if created else "exists"}')
        self.stdout.write(self.style.SUCCESS('Atlas seed complete.'))
