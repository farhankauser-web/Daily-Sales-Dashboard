"""
Amazon reporting periods — the grid this report runs on.

WHY A GRID AND NOT A DATE RANGE. Brand Analytics publishes on Amazon's week
(Sunday–Saturday) and nothing else. If the user could pick an arbitrary PPC
range, the market half of the report would have nothing matching to fetch, and
every comparison between the two would be a mismatch waiting to happen. Fixing
the selector to Amazon's own week removes that by construction: any week the
user can pick is a week Brand Analytics *could* have data for, so when it does,
the two sides line up exactly.

WEEK NUMBERING IS AMAZON'S, NOT ISO. Verified against Seller Central: Week 31 of
2026 is 2026-07-26 → 2026-08-01. The rule that reproduces it is *Sunday-start,
week 1 is the week containing Jan 1* — week 1 of 2026 begins 2025-12-28.
`date.isocalendar()` calls that same week 30 and would be wrong by one
everywhere. Never reach for it here. (The same trap sat in
`BASearchQueryWeekly.week_start`, whose help_text claimed Monday until it was
corrected.)

COMPLETENESS IS PART OF THE OPTION. A rolling window averages over missing days;
a fixed period exposes them. That is an improvement only if the gap is visible,
so every period carries its day coverage and a partial period says so. Without
that, a week with three days of data reads as a business collapse.

This module is scoped to the Search Intelligence Center. Other Pulse modules set
their own cadence for their own objective (`MKT-D-012`) and must not import it.
"""
from dataclasses import dataclass
from datetime import date, timedelta

# Amazon's week begins on Sunday. Python's weekday(): Monday=0 … Sunday=6.
_SUNDAY = 6

WEEKLY = 'weekly'
MONTHLY = 'monthly'
PERIOD_TYPES = [(WEEKLY, 'Weekly'), (MONTHLY, 'Monthly')]

# How many periods the selector offers. Bounded so the query stays cheap and the
# dropdown stays readable.
MAX_WEEKS = 26
MAX_MONTHS = 12


@dataclass
class Period:
    """One selectable reporting period, with everything the UI needs to warn."""
    key:        str      # '2026-W31' | '2026-07'
    ptype:      str
    label:      str      # 'Week 31 | 2026-07-26 – 2026-08-01'
    start:      date
    end:        date
    ads_days:   int = 0          # days of advertising data actually present
    span_days:  int = 0          # days the period covers
    has_ba:     bool = False     # Brand Analytics published for this period
    ba_weeks:   int = 0

    @property
    def complete(self) -> bool:
        return self.ads_days >= self.span_days

    @property
    def coverage_note(self) -> str:
        bits = [f'ads {self.ads_days}/{self.span_days}']
        bits.append('market ✓' if self.has_ba else 'market —')
        return ' · '.join(bits)


# ── Amazon week arithmetic ───────────────────────────────────────────────────

def week_start_of(d: date) -> date:
    """The Sunday that begins the Amazon week containing `d`."""
    return d - timedelta(days=(d.weekday() - _SUNDAY) % 7)


def _week1_start(year: int) -> date:
    """Week 1 is the Sunday-start week containing 1 January."""
    return week_start_of(date(year, 1, 1))


def week_number(d: date) -> tuple:
    """
    (year, week) under Amazon's numbering.

    The year is the one whose week 1 contains this week — so early-January days
    can belong to the previous year's final week, exactly as Amazon reports them.
    """
    start = week_start_of(d)
    for year in (start.year + 1, start.year, start.year - 1):
        w1 = _week1_start(year)
        if w1 <= start:
            return year, (start - w1).days // 7 + 1
    raise ValueError(d)


def week_bounds(year: int, week: int) -> tuple:
    start = _week1_start(year) + timedelta(days=7 * (week - 1))
    return start, start + timedelta(days=6)


def week_key(d: date) -> str:
    y, w = week_number(d)
    return f'{y}-W{w:02d}'


def month_key(d: date) -> str:
    return f'{d.year}-{d.month:02d}'


def parse_key(key: str) -> tuple:
    """'2026-W31' | '2026-07' → (ptype, start, end)."""
    if 'W' in key:
        y, w = key.split('-W')
        start, end = week_bounds(int(y), int(w))
        return WEEKLY, start, end
    y, m = key.split('-')
    start = date(int(y), int(m), 1)
    end = (date(start.year + (start.month == 12), (start.month % 12) + 1, 1)
           - timedelta(days=1))
    return MONTHLY, start, end


def label_for(ptype: str, start: date, end: date) -> str:
    if ptype == WEEKLY:
        _, w = week_number(start)
        return f'Week {w} | {start} – {end}'
    return f'{start:%B %Y} | {start} – {end}'


# ── Availability ─────────────────────────────────────────────────────────────

def _ba_weeks_for(marketplace: str, asins=None) -> set:
    """Week-start dates Brand Analytics has published, optionally per group."""
    from ..models import BASearchQueryWeekly
    qs = BASearchQueryWeekly.objects.filter(marketplace=marketplace)
    if asins:
        qs = qs.filter(asin__in=list(asins))
    return set(qs.order_by().values_list('week_start', flat=True).distinct())


def _ads_days_by_date(marketplace: str, lo: date, hi: date) -> set:
    from ..models import AdsSearchTermDailySnapshot
    return set(AdsSearchTermDailySnapshot.objects
               .filter(marketplace=marketplace, date__range=(lo, hi))
               .order_by().values_list('date', flat=True).distinct())


def available(marketplace: str, ptype: str = WEEKLY, asins=None,
              today: date = None) -> list:
    """
    Periods the user may select, newest first.

    Offered on ADVERTISING availability, marked with whether Brand Analytics
    exists — the decision recorded with the selector design. Anchoring strictly
    to Brand Analytics would have been the tidier rule and would also have left
    UK, UAE and KSA with no periods at all, since Brand Analytics covers only
    USA today. A week with no market data is still worth reporting on for its
    advertising; the label says which sections will be empty before the user
    commits to generating it.

    The in-progress period is never offered: a partial week is not a period.
    """
    from django.db.models import Max, Min
    from ..models import AdsSearchTermDailySnapshot

    today = today or date.today()
    span = AdsSearchTermDailySnapshot.objects.filter(marketplace=marketplace) \
        .aggregate(lo=Min('date'), hi=Max('date'))
    if not span['lo']:
        return []

    lo, hi = span['lo'], span['hi']
    ads_dates = _ads_days_by_date(marketplace, lo, hi)
    ba = _ba_weeks_for(marketplace, asins)

    out = []
    if ptype == WEEKLY:
        cur = week_start_of(hi)
        limit = MAX_WEEKS
        while cur >= week_start_of(lo) and limit > 0:
            end = cur + timedelta(days=6)
            # Only completed periods — the current week is still accruing.
            if end < today:
                days = len([d for d in ads_dates if cur <= d <= end])
                if days:
                    out.append(Period(
                        key=week_key(cur), ptype=WEEKLY,
                        label=label_for(WEEKLY, cur, end),
                        start=cur, end=end, ads_days=days, span_days=7,
                        has_ba=cur in ba, ba_weeks=1 if cur in ba else 0))
                    limit -= 1
            cur -= timedelta(days=7)
    else:
        cur = date(hi.year, hi.month, 1)
        limit = MAX_MONTHS
        while cur >= date(lo.year, lo.month, 1) and limit > 0:
            end = (date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
                   - timedelta(days=1))
            if end < today:
                days = len([d for d in ads_dates if cur <= d <= end])
                if days:
                    # A week belongs to the month its END falls in, so every
                    # week counts once and none is split across two months.
                    wks = [w for w in ba if cur <= w + timedelta(days=6) <= end]
                    out.append(Period(
                        key=month_key(cur), ptype=MONTHLY,
                        label=label_for(MONTHLY, cur, end),
                        start=cur, end=end, ads_days=days,
                        span_days=(end - cur).days + 1,
                        has_ba=bool(wks), ba_weeks=len(wks)))
                    limit -= 1
            cur = (cur - timedelta(days=1)).replace(day=1)

    return out


def default_period(periods: list):
    """
    The newest COMPLETE period that has market data, else the newest complete
    one, else the newest offered.

    Preferring a period with Brand Analytics means the default report is the
    one that can answer the market questions — the reason this module exists.
    """
    if not periods:
        return None
    for p in periods:
        if p.complete and p.has_ba:
            return p
    for p in periods:
        if p.complete:
            return p
    return periods[0]


def next_key(key: str, n: int = 1) -> str:
    """The period n steps after this one — the window an action is judged in."""
    ptype, start, end = parse_key(key)
    if ptype == WEEKLY:
        return week_key(start + timedelta(days=7 * n))
    y, m = start.year, start.month + n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y}-{m:02d}'


def bounds(key: str) -> tuple:
    """key → (start, end). Convenience for callers that only need the dates."""
    _, start, end = parse_key(key)
    return start, end


def resolve(key: str, marketplace: str, ptype: str = WEEKLY, asins=None):
    """key → Period (with availability filled in), or the default."""
    periods = available(marketplace, ptype, asins)
    if key:
        for p in periods:
            if p.key == key:
                return p
    return default_period(periods)
