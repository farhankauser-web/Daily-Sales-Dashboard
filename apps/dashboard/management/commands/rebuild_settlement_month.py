"""
apps/dashboard/management/commands/rebuild_settlement_month.py

Rebuild one region+month of P&L line actuals from the Settlement Flat File V2
reports — the single source of truth for the Management P&L.

WHY THIS EXISTS
    ingest_settlement_reports ADDS each report's amounts onto whatever is
    already stored for the month:

        row, _ = SettlementLineActual.objects.get_or_create(...)
        row.amount = (row.amount or 0) + <parsed amount>

    A month total that can only grow has no way to be correct twice and no way
    to self-heal once wrong. USA July 2026 had accumulated to $2,471,529
    against a true V2 figure of ~$1,308,577 — 1.89x.

    This command RECOMPUTES instead. It reads every settlement whose period
    overlaps the month, attributes each row by its own posted-date, dedups
    rows restated across overlapping settlements, and REPLACES the stored
    lines. Running it twice produces the same answer as running it once.

DEDUP
    Settlements overlap by design, so the same transaction legitimately
    appears in more than one report. But a single report can also contain two
    genuinely identical lines. A global set() would collapse those and
    UNDERCOUNT.

    So: count each row-signature per report, then take the MAX count across
    reports rather than the sum. Two reports each restating the same 2 rows
    contribute 2, not 4 — while a report holding 2 real duplicates still
    contributes 2.

CHANNEL
    'Non-Amazon US' rows are MCF (Walmart) fulfilment, not Amazon sales. They
    carry $0 principal but real unit counts, which corrupts units and ARPU on
    the Amazon column. Excluded by default.

USAGE
    python manage.py rebuild_settlement_month --marketplace usa --month 2026-07
    python manage.py rebuild_settlement_month --marketplace usa --month 2026-07 --dry-run
    python manage.py rebuild_settlement_month --marketplace usa --from 2026-04 --to 2026-08
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date as _date
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# ── ownership ───────────────────────────────────────────────────────────────
# This command replaces ONLY the line keys that Settlement V2 can actually
# produce — i.e. every key classify_settlement_row() can return. Anything else
# in SettlementLineActual belongs to another subsystem and is left untouched:
#
#   cogs                → cogs_recalc (V2 units x YOUR cost table). Amazon
#                         does not know your cost, so V2 can never rebuild it.
#                         Deleting it would send gross margin through the roof.
#   sales_tax,          → the classifier deliberately drops tax rows as
#   sales_tax_refunds,    pass-through (collected & remitted), so a V2 rebuild
#   promo_tax             cannot reproduce them. The VAT marketplaces' gross-up
#                         in pnl_engine needs them; USA (vat=0) does not.
#   cash_*              → Cash Flow page aggregates.
#
# Keep this in step with classify_settlement_row if new heads are added there.
V2_OWNED_KEYS = {
    'gross_sales', 'returns', 'promo', 'other_income',
    'commission', 'fba_fee', 'ppc',
    'storage_fee', 'subscription', 'account_management',
    'inbound_transportation', 'other_logistics',
    'awd_transportation', 'awd_processing', 'awd_storage',
}

# Settlement marketplace-name values that are NOT Amazon retail sales.
NON_AMAZON_CHANNELS = {'non-amazon us', 'non-amazon'}


def _month_arg(s: str) -> _date:
    try:
        y, m = str(s).split('-')[:2]
        return _date(int(y), int(m), 1)
    except (ValueError, IndexError):
        raise CommandError(f'Bad month {s!r} — expected YYYY-MM.')


def _month_end(d: _date) -> _date:
    return _date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _row_signature(r: dict) -> tuple:
    """Stable identity for one settlement transaction line."""
    return (
        (r.get('order-id') or '').strip(),
        (r.get('shipment-id') or '').strip(),
        (r.get('order-item-code') or r.get('merchant-order-item-id') or '').strip(),
        (r.get('adjustment-id') or '').strip(),
        (r.get('amount-type') or '').strip(),
        (r.get('amount-description') or '').strip(),
        (r.get('posted-date') or r.get('posted-date-time') or '')[:10],
        (r.get('amount') or '').strip(),
        (r.get('quantity-purchased') or '').strip(),
    )


class Command(BaseCommand):
    help = ('Recompute P&L line actuals for a region+month from Settlement '
            'Flat File V2 — replaces rather than accumulates.')

    def add_arguments(self, p):
        p.add_argument('--marketplace', required=True)
        p.add_argument('--month', help='YYYY-MM (single month)')
        p.add_argument('--from', dest='m_from', help='YYYY-MM range start')
        p.add_argument('--to', dest='m_to', help='YYYY-MM range end (inclusive)')
        p.add_argument('--dry-run', action='store_true',
                       help='Show what would be written; change nothing.')
        p.add_argument('--include-mcf', action='store_true',
                       help='Keep Non-Amazon US (MCF) rows in the Amazon column.')
        p.add_argument('--sleep', type=int, default=5,
                       help='Seconds between report downloads (rate limiting).')

    # ── report download with backoff ────────────────────────────────────────
    def _download(self, client, doc_id, sleep_s):
        for attempt in range(5):
            try:
                return client.download_settlement_report(doc_id)
            except Exception as exc:
                if '429' not in str(exc) or attempt == 4:
                    raise
                wait = sleep_s * (2 ** attempt)
                self.stdout.write(f'      429 — waiting {wait}s')
                time.sleep(wait)
        return []

    def handle(self, marketplace, month, m_from, m_to, dry_run,
               include_mcf, sleep, **_):
        from apps.dashboard.models import SettlementReport, SettlementLineActual
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient

        if month:
            months = [_month_arg(month)]
        elif m_from and m_to:
            a, b = _month_arg(m_from), _month_arg(m_to)
            months, cur = [], a
            while cur <= b:
                months.append(cur)
                cur = _month_end(cur)
        else:
            raise CommandError('Pass --month, or both --from and --to.')

        cfg = AmazonAPIConfig.objects.filter(
            marketplace=marketplace, is_active=True).first()
        if not cfg:
            raise CommandError(f'No active AmazonAPIConfig for {marketplace!r}.')
        client = SPAPIClient(cfg)
        native_ccy = (getattr(settings, 'AMAZON_MARKETPLACES', {})
                      .get(marketplace, {}).get('currency', 'USD'))

        for month_start in months:
            self._rebuild_one(
                client, SettlementReport, SettlementLineActual, SPAPIClient,
                marketplace, month_start, native_ccy,
                dry_run, include_mcf, sleep)

    # ── one month ───────────────────────────────────────────────────────────
    def _rebuild_one(self, client, SettlementReport, SettlementLineActual,
                     SPAPIClient, marketplace, month_start, native_ccy,
                     dry_run, include_mcf, sleep_s):
        month_end = _month_end(month_start)
        month_tag = f'{month_start:%Y-%m}'

        reports = SettlementReport.objects.filter(
            marketplace=marketplace, status='ok',
            end_date__gte=month_start, start_date__lt=month_end,
        ).order_by('start_date')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{marketplace.upper()}] {month_tag} — '
            f'{reports.count()} settlement(s) overlap this month'))

        if not reports.exists():
            self.stdout.write(self.style.WARNING(
                '  no settlements — nothing to rebuild, leaving stored lines alone'))
            return

        # signature → max occurrences seen in any single report
        best: dict[tuple, int] = {}
        payload: dict[tuple, dict] = {}
        skipped_mcf = 0

        for rep in reports:
            self.stdout.write(f'  reading {rep.start_date} → {rep.end_date} …')
            try:
                rows = self._download(client, rep.document_id, sleep_s)
            except Exception as exc:
                raise CommandError(
                    f'  download failed for {rep.report_id}: '
                    f'{type(exc).__name__}: {exc}\n'
                    f'  Nothing was written — re-run when the API frees up.')

            local: dict[tuple, int] = defaultdict(int)
            for r in rows:
                posted = (r.get('posted-date') or r.get('posted-date-time') or '')[:10]
                if not posted:
                    continue
                try:
                    pd = _date.fromisoformat(posted)
                except ValueError:
                    continue
                if not (month_start <= pd < month_end):
                    continue

                mkt = (r.get('marketplace-name') or '').strip().lower()
                if not include_mcf and mkt in NON_AMAZON_CHANNELS:
                    skipped_mcf += 1
                    continue

                sig = _row_signature(r)
                local[sig] += 1
                payload.setdefault(sig, r)

            for sig, n in local.items():
                if n > best.get(sig, 0):
                    best[sig] = n

            time.sleep(sleep_s)

        # ── classify + aggregate ────────────────────────────────────────────
        signed = defaultdict(float)
        units  = defaultdict(int)
        for sig, n in best.items():
            r = payload[sig]
            ttype = r.get('transaction-type') or ''
            desc  = r.get('amount-description') or ''
            atype = r.get('amount-type') or ''
            try:
                amount = float(r.get('amount') or 0)
            except (TypeError, ValueError):
                continue
            if amount == 0 and not (r.get('quantity-purchased') or '').strip():
                continue

            key = SPAPIClient.classify_settlement_row(ttype, atype, desc)
            if not key:
                continue

            signed[key] += amount * n
            dn = ''.join(desc.lower().split()).replace('-', '').replace('_', '')
            try:
                qty = int(float(r.get('quantity-purchased') or 0))
            except (TypeError, ValueError):
                qty = 0
            if key == 'gross_sales' and dn == 'principal' and qty > 0:
                units[key] += qty * n
            elif key == 'returns' and dn == 'principal':
                units[key] += abs(qty) * n

        income_keys = getattr(SPAPIClient, '_PNL_INCOME_KEYS',
                              {'gross_sales', 'other_income'})
        lines = {
            k: {'amount': round(v if k in income_keys else abs(v), 2),
                'units': units.get(k, 0)}
            for k, v in signed.items()
        }

        # ── report ──────────────────────────────────────────────────────────
        prior = {r.line_key: r for r in SettlementLineActual.objects.filter(
            marketplace=marketplace, month=month_start)}
        self.stdout.write(f'\n  {"LINE":<26}{"STORED":>16}{"REBUILT":>16}{"CHANGE":>12}')
        for k in sorted(set(lines) | (set(prior) & V2_OWNED_KEYS)):
            old = float(prior[k].amount) if k in prior else 0.0
            new = lines.get(k, {}).get('amount', 0.0)
            mult = (old / new) if new else 0.0
            self.stdout.write(
                f'  {k:<26}{old:>16,.2f}{new:>16,.2f}'
                f'{(f"{mult:.2f}x" if mult else "—"):>12}')

        untouched = sorted(set(prior) - V2_OWNED_KEYS)
        if untouched:
            self.stdout.write('\n  NOT owned by V2 — left exactly as-is:')
            for k in untouched:
                self.stdout.write(f'    {k:<26}{float(prior[k].amount):>16,.2f}')

        if skipped_mcf:
            self.stdout.write(self.style.WARNING(
                f'\n  excluded {skipped_mcf} Non-Amazon US (MCF) row(s)'))
        self.stdout.write(f'  deduped signatures: {len(best):,}')

        # A V2 key the rebuild produced nothing for would silently zero a real
        # figure — surface it rather than writing an unexplained 0.
        vanished = sorted((set(prior) & V2_OWNED_KEYS) - set(lines))
        if vanished:
            self.stdout.write(self.style.WARNING(
                f'  V2 keys with a stored value but no rebuilt rows: '
                f'{", ".join(vanished)} — will be removed'))

        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — nothing written'))
            return

        # ── replace ONLY the V2-owned keys ──────────────────────────────────
        with transaction.atomic():
            SettlementLineActual.objects.filter(
                marketplace=marketplace, month=month_start,
                line_key__in=V2_OWNED_KEYS,
            ).delete()

            SettlementLineActual.objects.bulk_create([
                SettlementLineActual(
                    marketplace=marketplace, month=month_start, line_key=k,
                    amount=Decimal(str(v['amount'])), units=v['units'],
                    currency=native_ccy, source_note='settlement_v2')
                for k, v in lines.items() if k in V2_OWNED_KEYS
            ])

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {month_tag} rebuilt — {len(lines)} line(s) from V2'))
