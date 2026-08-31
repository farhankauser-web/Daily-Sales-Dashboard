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

import gc
import hashlib
import time
from collections import defaultdict
from datetime import date as _date, timedelta
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


def _row_digest(r: dict) -> bytes:
    """
    Stable 8-byte identity for one settlement transaction line.

    Hashed rather than kept as a tuple of strings on purpose: this box has
    ~900MB of RAM and a single month can carry ~290k unique signatures. Holding
    those as 9-string tuples (let alone the source row dicts) exhausts memory
    and takes gunicorn down with it. 8 bytes each keeps the whole map in a few
    MB. Collision risk at ~3e5 keys over 64 bits is ~2e-9 — negligible.
    """
    sig = '\x1f'.join((
        (r.get('order-id') or '').strip(),
        (r.get('shipment-id') or '').strip(),
        (r.get('order-item-code') or r.get('merchant-order-item-id') or '').strip(),
        (r.get('adjustment-id') or '').strip(),
        (r.get('amount-type') or '').strip(),
        (r.get('amount-description') or '').strip(),
        (r.get('posted-date') or r.get('posted-date-time') or '')[:10],
        (r.get('amount') or '').strip(),
        (r.get('quantity-purchased') or '').strip(),
    ))
    return hashlib.blake2b(sig.encode('utf-8'), digest_size=8).digest()


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
        p.add_argument('--lookahead-days', type=int, default=60,
                       help='Also read settlements starting up to N days AFTER '
                            'month-end. Rows are attributed by posted-date, so a '
                            'later settlement can still carry a row posted in this '
                            'month (retroactive fees, reimbursements, promo '
                            'corrections). Reading only period-overlapping '
                            'settlements undercounts those. 0 = period overlap only.')

    # ── source a settlement: local cache first, then the API ────────────────
    def _fetch(self, client, marketplace, rep, sleep_s):
        """
        Rows for one settlement.

        A locally-cached copy wins over the API. Amazon expires report
        documents after a few months — USA April and May 2026 already return
        400 — but Seller Central still serves those files, and the upload view
        caches them here. Preferring the cache also spares the rate limit on
        months that have already been fetched by hand.
        """
        from apps.dashboard.settlement_cache import read_cached

        cached = read_cached(marketplace, rep.report_id)
        if cached is not None:
            self.stdout.write('[cached] ', ending='')
            return cached

        if not rep.document_id:
            raise CommandError(
                f'  {rep.report_id} has no document and no cached copy.\n'
                f'  Upload the flat file from Seller Central → Payments → '
                f'Reports Repository.')

        for attempt in range(5):
            try:
                return client.download_settlement_report(rep.document_id)
            except Exception as exc:
                if '429' not in str(exc) or attempt == 4:
                    raise
                wait = sleep_s * (2 ** attempt)
                self.stdout.write(f'      429 — waiting {wait}s')
                time.sleep(wait)
        return []

    def handle(self, marketplace, month, m_from, m_to, dry_run,
               include_mcf, sleep, lookahead_days, **_):
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

        # A month whose settlements cannot all be downloaded must be SKIPPED,
        # not partially rebuilt — rebuilding from 16 of 17 settlements would
        # silently undercount, which is worse than leaving the old figure and
        # saying so. But one dead document must not abort the whole range.
        done, failed = [], []
        for month_start in months:
            try:
                self._rebuild_one(
                    client, SettlementReport, SettlementLineActual, SPAPIClient,
                    marketplace, month_start, native_ccy,
                    dry_run, include_mcf, sleep, lookahead_days)
                done.append(month_start)
            except CommandError as exc:
                failed.append((month_start, str(exc).strip().splitlines()[0]))
                self.stdout.write(self.style.ERROR(
                    f'  ✗ {month_start:%Y-%m} SKIPPED — left unchanged'))

        if len(months) > 1 or failed:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING('SUMMARY'))
            if done:
                self.stdout.write(self.style.SUCCESS(
                    f'  rebuilt : {", ".join(f"{m:%Y-%m}" for m in done)}'))
            for m, why in failed:
                self.stdout.write(self.style.ERROR(f'  skipped : {m:%Y-%m} — {why}'))
            if failed:
                self.stdout.write(self.style.WARNING(
                    '\n  Skipped months still hold their OLD (inflated) figures.\n'
                    '  A 400 on a report document usually means Amazon has expired\n'
                    '  it — those months may not be rebuildable from V2 at all.'))

    # ── one month ───────────────────────────────────────────────────────────
    def _rebuild_one(self, client, SettlementReport, SettlementLineActual,
                     SPAPIClient, marketplace, month_start, native_ccy,
                     dry_run, include_mcf, sleep_s, lookahead_days=0):
        month_end = _month_end(month_start)
        month_tag = f'{month_start:%Y-%m}'

        # Rows are attributed by posted-date, so a settlement that STARTS after
        # month-end can still carry rows posted inside the month. Reading only
        # period-overlapping settlements silently undercounts those.
        window_end = month_end + timedelta(days=max(0, lookahead_days))

        reports = SettlementReport.objects.filter(
            marketplace=marketplace, status='ok',
            end_date__gte=month_start, start_date__lt=window_end,
        ).order_by('start_date')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{marketplace.upper()}] {month_tag} — {reports.count()} '
            f'settlement(s) (period overlap + {lookahead_days}d lookahead)'))

        if not reports.exists():
            self.stdout.write(self.style.WARNING(
                '  no settlements — nothing to rebuild, leaving stored lines alone'))
            return

        # digest → max occurrences seen in any SINGLE report
        best: dict[bytes, int] = {}
        # digest → (line_key, amount, signed_units) — compact; never the row.
        meta: dict[bytes, tuple] = {}
        skipped_mcf = 0

        for rep in reports:
            self.stdout.write(
                f'  reading {rep.start_date} → {rep.end_date} … ', ending='')
            self.stdout.flush()
            try:
                rows = self._fetch(client, marketplace, rep, sleep_s)
            except CommandError:
                raise
            except Exception as exc:
                expired = '400' in str(exc)
                raise CommandError(
                    f'\n  could not read {rep.report_id} '
                    f'({rep.start_date} → {rep.end_date}): '
                    f'{type(exc).__name__}: {exc}\n'
                    + (f'  Amazon has expired this document. Download the flat '
                       f'file from Seller Central → Payments → Reports '
                       f'Repository and upload it on the P&L page, then re-run.'
                       if expired else
                       f'  Nothing was written — re-run when the API frees up.'))

            local: dict[bytes, int] = defaultdict(int)
            kept = 0
            for r in rows:
                # Amazon localises dates too: DE posts '24.06.2026', not ISO.
                # date.fromisoformat() raised ValueError on every German row and
                # the bare `continue` dropped it, so the month looked empty and
                # the rebuild DELETED DE's stored P&L lines.
                pd = SPAPIClient.parse_settlement_date(
                    r.get('posted-date') or r.get('posted-date-time'))
                if pd is None:
                    continue
                if not (month_start <= pd < month_end):
                    continue

                mkt = (r.get('marketplace-name') or '').strip().lower()
                if not include_mcf and mkt in NON_AMAZON_CHANNELS:
                    skipped_mcf += 1
                    continue

                desc = r.get('amount-description') or ''
                key = SPAPIClient.classify_settlement_row(
                    r.get('transaction-type') or '', r.get('amount-type') or '', desc)
                if not key:
                    continue
                # Amazon localises the decimal separator: DE ships '-6,24'.
                # A bare float() raised ValueError and the bare `continue`
                # below discarded every German row in silence — 27 settlements,
                # 35,226 rows, a P&L of zero, and no error anywhere.
                amount = SPAPIClient.parse_settlement_amount(r.get('amount'))
                if amount is None:
                    continue
                qty_f = SPAPIClient.parse_settlement_amount(
                    r.get('quantity-purchased'))
                qty = int(qty_f) if qty_f is not None else 0
                if amount == 0 and qty == 0:
                    continue

                dn = ''.join(desc.lower().split()).replace('-', '').replace('_', '')
                if key == 'gross_sales' and dn == 'principal' and qty > 0:
                    u = qty
                elif key == 'returns' and dn == 'principal':
                    u = abs(qty)
                else:
                    u = 0

                dig = _row_digest(r)
                local[dig] += 1
                if dig not in meta:
                    meta[dig] = (key, amount, u)
                kept += 1

            n_rows = len(rows)
            # Drop the report before merging — a single settlement can be
            # 140k+ rows and this box cannot hold two of them at once.
            del rows
            gc.collect()

            for dig, n in local.items():
                if n > best.get(dig, 0):
                    best[dig] = n
            del local

            self.stdout.write(f'{n_rows:,} rows, {kept:,} in-month')
            time.sleep(sleep_s)

        # ── aggregate ───────────────────────────────────────────────────────
        signed = defaultdict(float)
        units  = defaultdict(int)
        n_signatures = len(best)
        for dig, n in best.items():
            key, amount, u = meta[dig]
            signed[key] += amount * n
            if u:
                units[key] += u * n
        del best, meta
        gc.collect()

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
        self.stdout.write(f'  deduped signatures: {n_signatures:,}')

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
