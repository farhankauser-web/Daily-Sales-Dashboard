"""
Atlas supply-chain alert sweep (cron: hourly).
  * RFQs past the 24h response TAT (incl. revalidations)
  * PO tracking stages past their TAT (alerted once per stage)
  * Articles whose stock covers less than their lead time (refill due)
"""
import json

from django.core.management.base import BaseCommand

from apps.atlas import supply
from apps.atlas.models import AtlasProduct
from apps.walmart_mcf.core import notify_admin


class Command(BaseCommand):
    help = 'Alert on RFQ TAT breaches, PO stage TAT breaches, refill-due articles.'

    def handle(self, **_):
        out = {'rfq_overdue': 0, 'po_stage_breaches': 0, 'refill_due': 0}

        overdue = supply.overdue_rfqs()
        out['rfq_overdue'] = len(overdue)
        if overdue:
            notify_admin(
                f'Atlas: {len(overdue)} RFQ(s) past the 24h TAT',
                '\n'.join(f'{r.reference} ({r.company.code}) — {r.status}, '
                          f'due {r.tat_deadline:%b %d %H:%M}'
                          for r in overdue[:20]))

        breaches = [s for s in supply.breached_stages() if not s.alerted]
        out['po_stage_breaches'] = len(breaches)
        for s in breaches:
            notify_admin(
                f'Atlas: PO {s.po.reference} stuck in "{s.name}"',
                f'Stage started {s.started_at:%b %d}, TAT {s.tat_days}d, '
                f'deadline was {s.deadline:%b %d}.')
            s.alerted = True
            s.save(update_fields=['alerted'])

        due = []
        for p in AtlasProduct.objects.filter(is_active=True,
                                             sell_through_daily__gt=0):
            f = supply.forecast_product(p)
            if f['refill_due']:
                due.append((p, f))
        out['refill_due'] = len(due)
        if due:
            notify_admin(
                f'Atlas: {len(due)} article(s) need a refill order',
                '\n'.join(f'{p.sku} ({p.company.code}): stock {p.stock_qty}, '
                          f'covers {f["cover_days"]}d < lead {f["lead_days"]}d '
                          f'→ order ~{f["reorder_qty"]}'
                          for p, f in due[:25]))

        self.stdout.write(json.dumps(out))
