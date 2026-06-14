"""
import_hourly_csv — Bulk-load a Seller Central hourly CSV from the shell.

Same writer the UI uses (apps.dashboard.manual_hourly_importer) so audit rows,
sync-log entries, and upserts behave identically.

Usage:
    python manage.py import_hourly_csv \\
        --marketplace usa --ad-type sp \\
        --file ~/Downloads/SP-2026-06-01_to_2026-06-07.csv

    # Bulk-import a directory of files at once:
    python manage.py import_hourly_csv \\
        --marketplace usa --ad-type sp \\
        --dir ~/Downloads/sp-hourly/
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ('Import Seller Central hourly CSV(s) into PPCCampaignHourlySnapshot. '
            'Each file must be ≤ 14 days (GUI cap).')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--ad-type', required=True, choices=['sp', 'sb', 'sd'])
        parser.add_argument('--file', help='Path to one CSV file')
        parser.add_argument('--dir',  help='Path to a directory of .csv files '
                                            '(all imported with the same --ad-type)')

    def handle(self, *args, **opts):
        from apps.dashboard.manual_hourly_importer import import_hourly_csv_bytes

        if not opts['file'] and not opts['dir']:
            self.stderr.write(self.style.ERROR('Provide --file or --dir.'))
            return

        files: list[Path] = []
        if opts['file']:
            files.append(Path(opts['file']).expanduser())
        else:
            d = Path(opts['dir']).expanduser()
            if not d.is_dir():
                self.stderr.write(self.style.ERROR(f'Not a directory: {d}'))
                return
            files.extend(sorted(d.glob('*.csv')))

        if not files:
            self.stderr.write(self.style.WARNING('No CSV files found.'))
            return

        total_imported = 0
        for f in files:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n  • {f.name}'))
            try:
                data = f.read_bytes()
            except OSError as e:
                self.stderr.write(self.style.ERROR(f'    cannot read: {e}'))
                continue

            result = import_hourly_csv_bytes(
                marketplace       = opts['marketplace'],
                ad_type           = opts['ad_type'],
                file_bytes        = data,
                original_filename = f.name,
            )
            pr = result['parse_result']
            dr = result['date_range']
            style = self.style.SUCCESS if result['status'] == 'ok' else self.style.ERROR
            self.stdout.write(style(
                f"    status={result['status']}  rows={result['rows_imported']}  "
                f"days={result['days_covered']}  range={dr}  "
                f"file_rows={pr.rows_in_file}  skipped={pr.rows_skipped}"
            ))
            if pr.errors:
                for err in pr.errors:
                    self.stdout.write(self.style.WARNING(f'      ! {err}'))
            if pr.columns_missing:
                self.stdout.write(self.style.WARNING(
                    f'      missing columns: {pr.columns_missing}'))
            total_imported += result['rows_imported']

        self.stdout.write(self.style.SUCCESS(
            f'\n✅  {total_imported} row(s) imported across {len(files)} file(s).\n'))
