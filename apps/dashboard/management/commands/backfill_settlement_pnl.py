"""
Backfill SettlementLineActual (the Management P&L's settled-actuals source)
from settlement reports already ingested into SettlementReport.

The original FBA-drift ingest only extracted the per-SKU FBA fee. This command
re-downloads each stored settlement document, runs the full P&L-line classifier
(SPAPIClient.extract_pnl_lines), de-duplicates by settlement-id (Amazon issues
paired/overlapping report requests for the same settlement), and writes fresh
per-(marketplace, month, line_key) rows.

Idempotent: clears existing SettlementLineActual for the marketplace before
rewriting, so re-runs never double-count.

Usage:
    manage.py backfill_settlement_pnl --marketplace usa
    manage.py backfill_settlement_pnl --marketplace usa --dump-raw /tmp/settle
"""
from __future__ import annotations

import csv as _csv
import os as _os
from collections import defaultdict
from datetime import date as _date

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Re-parse stored settlement reports → SettlementLineActual (P&L actuals).'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--dump-raw', default=None, metavar='DIR',
                             help='Also save each raw settlement TSV into DIR.')

    def handle(self, marketplace, dump_raw, **_):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient
        from apps.dashboard.models import SettlementReport, SettlementLineActual

        cfg = AmazonAPIConfig.objects.filter(
            marketplace=marketplace, is_active=True).first()
        if not cfg:
            self.stderr.write(f'No active config for {marketplace}.')
            return
        client = SPAPIClient(cfg)
        native_ccy = (settings.AMAZON_MARKETPLACES.get(marketplace, {})
                      .get('currency', 'USD'))

        if dump_raw:
            _os.makedirs(dump_raw, exist_ok=True)

        reports = list(SettlementReport.objects.filter(
            marketplace=marketplace, status='ok').exclude(document_id='')
            .order_by('start_date'))
        self.stdout.write(f'Found {len(reports)} settled reports to re-parse.')

        # settlement-id → its rows (keep the richest copy when duplicated)
        by_settlement: dict[str, list] = {}
        for rep in reports:
            try:
                rows = client.download_settlement_report(rep.document_id)
            except Exception as exc:
                self.stderr.write(f'  ✗ {rep.report_id}: download failed — {exc}')
                continue
            if not rows:
                continue
            sid = (rows[0].get('settlement-id') or rep.report_id).strip()
            # Prefer the version with more rows (the complete settlement)
            if sid not in by_settlement or len(rows) > len(by_settlement[sid]):
                by_settlement[sid] = rows
            self.stdout.write(
                f'  · {rep.start_date}→{rep.end_date}  sid={sid[:14]}  rows={len(rows)}')
            if dump_raw:
                fn = _os.path.join(dump_raw, f'settle_{marketplace}_{rep.report_id}.tsv')
                with open(fn, 'w', newline='', encoding='utf-8') as fh:
                    w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter='\t')
                    w.writeheader(); w.writerows(rows)

        self.stdout.write(f'Distinct settlements after de-dup: {len(by_settlement)}')

        # Run the classifier over the concatenated, de-duped row set
        all_rows = [r for rows in by_settlement.values() for r in rows]
        result = SPAPIClient.extract_pnl_lines(all_rows)
        lines    = result['lines']
        unmapped = result['unmapped']

        # Write fresh
        with transaction.atomic():
            SettlementLineActual.objects.filter(marketplace=marketplace).delete()
            n = 0
            for (month_str, line_key), vals in lines.items():
                try:
                    m = _date.fromisoformat(month_str + '-01')
                except ValueError:
                    continue
                SettlementLineActual.objects.create(
                    marketplace=marketplace, month=m, line_key=line_key,
                    amount=round(vals['amount'], 2), units=int(vals['units']),
                    currency=native_ccy, source_note='settlement')
                n += 1

        self.stdout.write(self.style.SUCCESS(
            f'DONE: wrote {n} SettlementLineActual rows for {marketplace}.'))

        # Surface months covered + any real unmapped (tax pairs net ~0)
        months = sorted({k[0] for k in lines})
        self.stdout.write(f'Months covered: {", ".join(months)}')
        big_unmapped = {d: a for d, a in unmapped.items() if abs(a) > 100}
        if big_unmapped:
            self.stdout.write(self.style.WARNING('Unmapped > $100 (review):'))
            for d, a in sorted(big_unmapped.items(), key=lambda x: -abs(x[1])):
                self.stdout.write(f'   {d:48.48s} ${a:,.2f}')
