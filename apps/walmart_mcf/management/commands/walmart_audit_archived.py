"""Audit already-archived Walmart orders for premature archiving.

An order reaches TRACKING_UPLOADED (and thus Archive) only when its tracking was
pushed to Walmart. Before the partial-shipment fix, a multi-SKU order could get
there after just ONE of its SKUs shipped — hiding the un-shipped SKUs in Archive.

This walks every archived (TRACKING_UPLOADED / COMPLETED) order and flags those
that are NOT actually fully shipped (see pipeline._order_fully_shipped). With
--apply it moves each flagged order back to SHIPPED so the normal pipeline
(check_status → upload_tracking) resumes monitoring it and only re-archives it
once every SKU has shipped.

    python manage.py walmart_audit_archived           # dry-run report
    python manage.py walmart_audit_archived --apply    # reopen the partials
"""
from django.core.management.base import BaseCommand

from apps.walmart_mcf.models import WalmartOrder, WalmartOrderState as S
from apps.walmart_mcf.pipeline import _order_fully_shipped
from apps.walmart_mcf.state import transition

ARCHIVE_STATUSES = [S.TRACKING_UPLOADED, S.COMPLETED]


class Command(BaseCommand):
    help = 'Find (and optionally reopen) multi-SKU orders archived before all SKUs shipped.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Reopen flagged orders (TRACKING_UPLOADED → SHIPPED).')

    def handle(self, apply, **_):
        qs = (WalmartOrder.objects
              .filter(status__in=ARCHIVE_STATUSES, mcf__isnull=False)
              .select_related('mcf')
              .prefetch_related('items', 'mcf__packages'))

        reopenable, review, reopened = [], [], 0
        for order in qs:
            # Issue #1 is strictly about MULTI-SKU orders archived before every
            # SKU shipped. A single-SKU order with a shipped package is complete
            # — never flag it (this also skips legacy single-line restored orders
            # whose historical amazon_status isn't a live MCF status).
            if order.items.count() <= 1:
                continue
            if _order_fully_shipped(order):
                continue                       # correctly archived
            # TRACKING_UPLOADED → still has a live pipeline path, safe to reopen.
            # COMPLETED (terminal) is almost always a historical/restored order
            # whose Amazon status is no longer queryable — reopening would strand
            # it in Active, so we only report those for manual review.
            (reopenable if order.status == S.TRACKING_UPLOADED
             else review).append(order)

        def _line(o, tag):
            self.stdout.write(
                f'  {tag}  PO {o.purchase_order_id}  status={o.status}  '
                f'items={o.items.count()}  packages={o.mcf.packages.count()}  '
                f'amazon={o.mcf.amazon_status or "?"}')

        if reopenable:
            self.stdout.write(self.style.WARNING(
                'Reopenable partials (TRACKING_UPLOADED → re-monitor):'))
            for o in reopenable:
                _line(o, 'REOPEN ')
                if apply and transition(
                        o, S.SHIPPED, 'walmart_audit_archived',
                        {'note': 'reopened — not all SKUs shipped'},
                        from_states=[S.TRACKING_UPLOADED]):
                    reopened += 1
        if review:
            self.stdout.write(self.style.WARNING(
                '\nCOMPLETED multi-SKU orders with a single package '
                '(historical — review manually, NOT auto-reopened):'))
            for o in review:
                _line(o, 'REVIEW ')

        self.stdout.write(self.style.WARNING(
            f'\n{len(reopenable)} reopenable, {len(review)} for manual review.'))
        if apply:
            self.stdout.write(self.style.SUCCESS(
                f'Reopened {reopened} order(s) → SHIPPED for re-monitoring.'))
        elif reopenable:
            self.stdout.write('Re-run with --apply to reopen the TRACKING_UPLOADED ones.')
