"""
apps/dashboard/settlement_cache.py — locally-held Settlement Flat File V2.

WHY THIS EXISTS
    Amazon expires report DOCUMENTS after a few months. The settlement is
    still listed by the API, and still downloadable from Seller Central →
    Payments → Reports Repository, but /reports/.../documents/<id> starts
    returning 400. USA April and May 2026 are already past that line:

        download failed for 836877020585: 400 Client Error

    Those months therefore cannot be rebuilt from the API at all, and
    rebuild_settlement_month refuses to write a month it can only partially
    read — a half-built month is worse than a visibly stale one.

    So the file can be fetched by hand from Seller Central and uploaded. This
    module is the shared contract between the upload view and the rebuild
    command: same directory, same naming, same reader.

The cache is keyed by report_id so the rebuild can ask for exactly the
settlement it needs. Uploaded files that match no known report get a
SettlementReport row created for them by the upload view.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from django.conf import settings

CACHE_ROOT = 'settlement_v2_cache'


def cache_dir(marketplace: str) -> Path:
    d = Path(settings.MEDIA_ROOT) / CACHE_ROOT / marketplace
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(marketplace: str, report_id: str) -> Path:
    safe = ''.join(c for c in str(report_id) if c.isalnum() or c in '-_.')
    return cache_dir(marketplace) / f'{safe}.tsv'


def has_cached(marketplace: str, report_id: str) -> bool:
    return cache_path(marketplace, report_id).exists()


def read_cached(marketplace: str, report_id: str) -> list[dict] | None:
    """Parsed rows for a cached settlement, or None if not held locally."""
    p = cache_path(marketplace, report_id)
    if not p.exists():
        return None
    with p.open('r', encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh, delimiter='\t'))


def write_cached(marketplace: str, report_id: str, raw: bytes) -> Path:
    p = cache_path(marketplace, report_id)
    p.write_bytes(raw)
    return p


def parse_bytes(raw: bytes) -> list[dict]:
    """
    Parse a settlement flat file. Amazon ships it tab-delimited; a file that
    has been through Excel may come back comma-delimited, so sniff rather than
    assume and hand back an empty list instead of a wall of junk columns.
    """
    text = raw.decode('utf-8-sig', errors='replace')
    head = text.split('\n', 1)[0]
    delim = '\t' if head.count('\t') >= head.count(',') else ','
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def describe(rows: list[dict]) -> dict:
    """
    Identity of an uploaded settlement: its settlement-id and the span of
    posted-dates it actually contains. The posted range is what matters for
    matching — the file's own header dates can be absent in edited copies.
    """
    from datetime import date as _date
    sids, dates = set(), []
    for r in rows:
        s = (r.get('settlement-id') or '').strip()
        if s:
            sids.add(s)
        d = (r.get('posted-date') or r.get('posted-date-time') or '')[:10]
        if d:
            try:
                dates.append(_date.fromisoformat(d))
            except ValueError:
                pass
    return {
        'settlement_ids': sorted(sids),
        'first_posted': min(dates) if dates else None,
        'last_posted': max(dates) if dates else None,
        'rows': len(rows),
    }
