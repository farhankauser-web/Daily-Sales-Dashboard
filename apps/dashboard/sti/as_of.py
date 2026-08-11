"""
Data As Of — what each dataset covers, measured rather than assumed.

`MKT-D-012`: every metric uses the cadence its business question needs, and the
single obligation in return is transparency. This module produces the stamps
that discharge that obligation — source, reporting period, and as-of date, per
dataset.

It does NOT align anything. There is no common window here and no clamping. Each
source reports where it actually is, and the UI shows all of them side by side so
a reader can weigh a card without having to know the pipeline.

Availability is MEASURED (`max(date)` per source per marketplace), never derived
from a declared lag. `INFRA-001` records that the production crontab is
hand-maintained and drifted, so a nominal-lag constant would encode an
assumption about a schedule nobody can verify, and would fail silently the day a
job stops.
"""
from django.db.models import Max


def _fmt(d):
    return d.isoformat() if d else None


def measure(scope, ba_window=None, valuation=None, inventory=None) -> dict:
    """
    Return {source_key: {label, cadence, period, as_of, note}}.

    `period` is the span the report actually used for that source; `as_of` is
    the newest data the source holds, so a reader can see both what was used and
    how fresh the source is.
    """
    from ..models import (
        AdsSearchTermDailySnapshot, BASearchQueryWeekly, DailySkuSnapshot,
    )

    mp = scope.marketplace
    out = {}

    # ── Advertising — operational, T-2 ───────────────────────────────────────
    ads_latest = (AdsSearchTermDailySnapshot.objects
                  .filter(marketplace=mp).aggregate(d=Max('date'))['d'])
    out['ads'] = {
        'label':   'Advertising',
        'cadence': 'daily',
        'period':  {'start': _fmt(scope.date_from), 'end': _fmt(scope.date_to)},
        'as_of':   _fmt(ads_latest),
        'note':    'Spend, clicks and ad sales for the selected window. '
                   'Ad sales in the last few days still accrue under 7-day attribution.',
    }

    # ── Brand Analytics — strategic, latest completed week(s) ────────────────
    ba_latest = BASearchQueryWeekly.objects.filter(marketplace=mp).aggregate(d=Max('week_end'))['d']
    if ba_window and ba_window.get('has_data'):
        period = {'start': _fmt(ba_window['start']), 'end': _fmt(ba_window['end'])}
        note = (f"{ba_window['count']} completed week"
                f"{'s' if ba_window['count'] != 1 else ''} — the latest Amazon has published "
                f"for these ASINs.")
        if ba_window.get('stale'):
            note += (f" Predates the advertising window by "
                     f"{ba_window['staleness_days']} days; market share is a structural "
                     f"reading, trends are withheld.")
    else:
        period, note = None, 'No completed weeks for these ASINs.'
    out['ba_sqp'] = {
        'label': 'Market share (Brand Analytics)', 'cadence': 'weekly',
        'period': period, 'as_of': _fmt(ba_latest), 'note': note,
    }

    # ── Valuation basis — structural, long trailing window ───────────────────
    if valuation:
        out['valuation'] = {
            'label':   'Margin & selling price',
            'cadence': 'daily (settled), trailing',
            'period':  {'start': _fmt(valuation.get('start')),
                        'end':   _fmt(valuation.get('end'))},
            'as_of':   _fmt(valuation.get('as_of')),
            'note':    (f"{valuation['days']} selling days across "
                        f"{valuation['skus_sold']} of {valuation['skus_total']} group SKUs. "
                        f"Deliberately independent of the report window — margin and price "
                        f"are structural, not period-sensitive."
                        if valuation.get('trusted') else
                        'Not enough settled revenue to measure; a fallback rate is in use.'),
        }

    # ── Group revenue — its own window ───────────────────────────────────────
    rev_latest = DailySkuSnapshot.objects.filter(marketplace=mp).aggregate(d=Max('date'))['d']
    out['sku_revenue'] = {
        'label': 'Settled revenue', 'cadence': 'daily (settled)',
        'period': {'start': _fmt(scope.date_from), 'end': _fmt(min(scope.date_to, rev_latest))}
                  if rev_latest else None,
        'as_of': _fmt(rev_latest),
        'note': 'Order-date per-SKU revenue. Lags advertising, so figures combining the '
                'two use only the days both cover.',
    }

    # ── Inventory — a snapshot, no period at all ─────────────────────────────
    inv_as_of = (inventory or {}).get('as_of')
    out['inventory'] = {
        'label': 'Inventory cover', 'cadence': 'snapshot',
        'period': None, 'as_of': inv_as_of,
        'note': 'Latest stock position — a point in time, not a period. Gates spend '
                'recommendations.' if inv_as_of else 'No inventory snapshot for this group.',
    }

    return out


def overlap(scope, source_stamp) -> tuple:
    """
    The days two datasets both cover, for the one place a ratio needs it.

    Not an alignment mechanism — `MKT-D-012` rejects those. A ratio whose
    numerator and denominator span different periods is simply wrong arithmetic,
    so paid share (ad sales ÷ settled revenue) uses this and nothing else does.
    """
    period = source_stamp.get('period')
    if not period or not period.get('end'):
        return None, None
    from datetime import date
    end = date.fromisoformat(period['end'])
    return scope.date_from, min(scope.date_to, end)
