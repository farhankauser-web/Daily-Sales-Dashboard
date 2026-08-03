"""
Command Center — widget registry + data producers.

WIDGET_CATALOG is the single source of truth for what can be placed on the
canvas. get_widget_data() dispatches a widget key → JSON payload, reusing the
existing app's models/services. Every producer is wrapped so one failing widget
degrades to an error card instead of breaking the page.
"""
from datetime import timedelta
from django.db.models import Sum, Count, Max
from django.utils import timezone

MARKETPLACES = ['usa', 'uk', 'ae', 'sa', 'ca', 'de']
FLAG = {'usa': '🇺🇸', 'uk': '🇬🇧', 'ae': '🇦🇪', 'sa': '🇸🇦', 'ca': '🇨🇦', 'de': '🇩🇪'}

# ── Catalog ────────────────────────────────────────────────────────────────
# w/h are Gridstack cells (12-col grid). category groups them in the drawer.
WIDGET_CATALOG = {
    'kpi': {
        'title': 'KPI tile', 'category': 'Performance', 'icon': '＄',
        'desc': 'One metric + trend + delta', 'w': 3, 'h': 2, 'minW': 2, 'minH': 2,
        'config': {'metric': 'revenue'}},   # revenue|gross_margin|tacos|units|ppc
    'revenue_trend': {
        'title': 'Revenue trend', 'category': 'Performance', 'icon': '◠',
        'desc': 'Daily revenue, last 30 days', 'w': 8, 'h': 3, 'minW': 4, 'minH': 3},
    'marketplace_split': {
        'title': 'Marketplace split', 'category': 'Performance', 'icon': '◔',
        'desc': 'Revenue share by region', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'scorecard': {
        'title': 'Marketplace scorecard', 'category': 'Performance', 'icon': '▤',
        'desc': 'Regions × KPIs', 'w': 8, 'h': 3, 'minW': 5, 'minH': 3},
    'ppc_vs_sales': {
        'title': 'PPC vs sales', 'category': 'Advertising', 'icon': '◎',
        'desc': 'Spend, sales & TACoS', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'top_skus': {
        'title': 'Top SKUs by profit', 'category': 'Advertising', 'icon': '▦',
        'desc': 'Best contributors, MTD', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'hourly_heatmap': {
        'title': 'Hourly heatmap', 'category': 'Advertising', 'icon': '▩',
        'desc': 'Contribution margin by hour', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'profit_alerts': {
        'title': 'Profit alerts', 'category': 'Profit & ops', 'icon': '⚠',
        'desc': 'Critical / watch / opportunity', 'w': 4, 'h': 4, 'minW': 3, 'minH': 3},
    'ai_recs': {
        'title': 'AI recommendations', 'category': 'Profit & ops', 'icon': '◍',
        'desc': 'Daily action list', 'w': 4, 'h': 3, 'minW': 3, 'minH': 2},
    'inventory_risk': {
        'title': 'Inventory risk', 'category': 'Profit & ops', 'icon': '▧',
        'desc': 'Low days-of-cover & stockouts', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'cash_runway': {
        'title': 'Cash flow runway', 'category': 'Profit & ops', 'icon': '▰',
        'desc': 'Balance + container timing', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'walmart_mcf': {
        'title': 'Walmart MCF status', 'category': 'Channels', 'icon': '🛒',
        'desc': 'Order pipeline health', 'w': 4, 'h': 2, 'minW': 3, 'minH': 2},
    'ba_share': {
        'title': 'Brand-Analytics share', 'category': 'Channels', 'icon': '◍',
        'desc': 'Search-query & market share', 'w': 4, 'h': 3, 'minW': 3, 'minH': 3},
    'container_timeline': {
        'title': 'Container / PO timeline', 'category': 'Channels', 'icon': '🚢',
        'desc': 'Incoming shipments + payments', 'w': 8, 'h': 3, 'minW': 4, 'minH': 2},
    'data_freshness': {
        'title': 'Data freshness', 'category': 'System', 'icon': '◷',
        'desc': 'Which syncs are current', 'w': 4, 'h': 3, 'minW': 3, 'minH': 2},
}

# Default = ALL widgets, arranged into a sensible full board (12-col grid).
DEFAULT_LAYOUT = [
    {'key': 'kpi', 'x': 0, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'revenue'}},
    {'key': 'kpi', 'x': 3, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'gross_margin'}},
    {'key': 'kpi', 'x': 6, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'tacos'}},
    {'key': 'kpi', 'x': 9, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'units'}},
    {'key': 'revenue_trend',     'x': 0, 'y': 2, 'w': 8, 'h': 3},
    {'key': 'marketplace_split', 'x': 8, 'y': 2, 'w': 4, 'h': 3},
    {'key': 'scorecard',         'x': 0, 'y': 5, 'w': 8, 'h': 3},
    {'key': 'profit_alerts',     'x': 8, 'y': 5, 'w': 4, 'h': 4},
    {'key': 'ppc_vs_sales',      'x': 0, 'y': 8, 'w': 4, 'h': 3},
    {'key': 'top_skus',          'x': 4, 'y': 8, 'w': 4, 'h': 3},
    {'key': 'hourly_heatmap',    'x': 0, 'y': 11, 'w': 8, 'h': 3},
    {'key': 'ai_recs',           'x': 8, 'y': 9, 'w': 4, 'h': 3},
    {'key': 'inventory_risk',    'x': 8, 'y': 12, 'w': 4, 'h': 3},
    {'key': 'cash_runway',       'x': 0, 'y': 14, 'w': 4, 'h': 3},
    {'key': 'ba_share',          'x': 4, 'y': 14, 'w': 4, 'h': 3},
    {'key': 'container_timeline', 'x': 0, 'y': 17, 'w': 8, 'h': 3},
    {'key': 'walmart_mcf',       'x': 8, 'y': 15, 'w': 4, 'h': 2},
    {'key': 'data_freshness',    'x': 8, 'y': 17, 'w': 4, 'h': 2},
]


# ── helpers ─────────────────────────────────────────────────────────────────
def _DM():
    from apps.dashboard.models import DailyMetric
    return DailyMetric


def _latest_date():
    d = _DM().objects.aggregate(m=Max('date'))['m']
    return d or timezone.localdate()


def _mp_now(mp):
    """Current time in a marketplace's own timezone (falls back to server TZ)."""
    from django.conf import settings
    from datetime import datetime
    tz_name = None
    if mp and mp != 'all':
        tz_name = (settings.AMAZON_MARKETPLACES.get(mp) or {}).get('timezone')
    tz_name = tz_name or settings.TIME_ZONE
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now()


def _local_today(mp):
    return _mp_now(mp).date()


def _local_hour(mp):
    return _mp_now(mp).hour


def _mp_filter(qs, cfg):
    mp = (cfg or {}).get('marketplace')
    return qs.filter(marketplace=mp) if mp and mp != 'all' else qs


def _ams_ppc(start, end, cfg=None):
    """
    {(marketplace, date): spend} — best live PPC figure per region/day.

    DailyMetric.ppc_spend is only written by the daily rollup, so the current
    (partial) day sits at 0 there — which is why the scorecard rendered PPC $0
    and TACoS 0.0% for every region. Two live sources fill that gap, and we
    take the larger, matching the max(AMS, daily) rule used elsewhere:

      * PPCCampaignHourlySnapshot — the AMS stream. Real-time, but only for
        marketplaces with a bucket in settings.AMS_S3 (today: USA only).
      * PPCCampaignSnapshot — what sync_today_ppc pulls from the Ads API every
        30 min. Covers all four regions, so UK/AE/SA depend on it entirely.
    """
    out = {}
    mp = (cfg or {}).get('marketplace')
    for model_path in ('PPCCampaignHourlySnapshot', 'PPCCampaignSnapshot'):
        try:
            from apps.dashboard import models as m
            P = getattr(m, model_path)
        except Exception:
            continue
        qs = P.objects.filter(date__range=(start, end))
        if mp and mp != 'all':
            qs = qs.filter(marketplace=mp)
        for r in qs.values('marketplace', 'date').annotate(s=Sum('spend')):
            k = (r['marketplace'], r['date'])
            out[k] = max(out.get(k, 0.0), float(r['s'] or 0))
    return out


def _ams_ppc_by_date(start, end, cfg=None):
    """{date: spend} — _ams_ppc collapsed across marketplaces."""
    out = {}
    for (_mp, d), v in _ams_ppc(start, end, cfg).items():
        out[d] = out.get(d, 0.0) + v
    return out


# ── producers ───────────────────────────────────────────────────────────────
_METRICS = {
    'revenue':      {'label': 'Revenue',      'field': 'revenue',      'fmt': 'currency'},
    'gross_margin': {'label': 'Gross Margin', 'field': 'gross_margin', 'fmt': 'currency'},
    'units':        {'label': 'Units',        'field': 'units',        'fmt': 'int'},
    'ppc':          {'label': 'PPC Spend',    'field': 'ppc_spend',    'fmt': 'currency'},
}


def w_kpi(user, cfg):
    DM = _DM(); d = _latest_date(); metric = (cfg or {}).get('metric', 'revenue')
    start = d - timedelta(days=13)
    if metric == 'tacos':
        rows = (_mp_filter(DM.objects.filter(date__range=(start, d)), cfg)
                .values('date').annotate(r=Sum('revenue'), p=Sum('ppc_spend')).order_by('date'))
        ams = _ams_ppc_by_date(start, d, cfg)
        spark = [round(max(float(x['p'] or 0), ams.get(x['date'], 0.0)) / float(x['r']) * 100, 2)
                 if x['r'] else 0 for x in rows]
        cur = spark[-1] if spark else 0
        prev = sum(spark[:-1]) / max(len(spark) - 1, 1) if len(spark) > 1 else cur
        return {'label': 'TACoS · latest day', 'value': cur, 'format': 'pct',
                'delta': (cur - prev), 'delta_unit': 'pt', 'delta_good': 'down',
                'delta_label': 'vs 14d avg', 'spark': spark}
    m = _METRICS.get(metric, _METRICS['revenue']); f = m['field']
    rows = list(_mp_filter(DM.objects.filter(date__range=(start, d)), cfg)
                .values('date').annotate(s=Sum(f)).order_by('date'))
    spark = [float(x['s'] or 0) for x in rows]
    if metric == 'ppc':
        ams = _ams_ppc_by_date(start, d, cfg)
        spark = [max(v, ams.get(x['date'], 0.0)) for v, x in zip(spark, rows)]
    cur = spark[-1] if spark else 0
    prev = sum(spark[:-1]) / max(len(spark) - 1, 1) if len(spark) > 1 else cur
    delta = ((cur - prev) / prev * 100) if prev else 0
    return {'label': f"{m['label']} · latest day", 'value': cur, 'format': m['fmt'],
            'delta': delta, 'delta_unit': '%', 'delta_good': 'up',
            'delta_label': 'vs 14d avg', 'spark': spark}


def w_revenue_trend(user, cfg):
    DM = _DM(); end = _latest_date(); start = end - timedelta(days=29)
    rows = (_mp_filter(DM.objects.filter(date__range=(start, end)), cfg)
            .values('date').annotate(rev=Sum('revenue')).order_by('date'))
    return {'labels': [r['date'].strftime('%b %d') for r in rows],
            'data': [float(r['rev'] or 0) for r in rows]}


def w_marketplace_split(user, cfg):
    DM = _DM(); end = _latest_date(); start = end - timedelta(days=6)
    out = []
    for mp in MARKETPLACES:
        rev = float(DM.objects.filter(marketplace=mp, date__range=(start, end))
                    .aggregate(s=Sum('revenue'))['s'] or 0)
        if rev > 0:
            out.append({'mp': mp, 'flag': FLAG.get(mp, ''), 'revenue': rev})
    out.sort(key=lambda x: -x['revenue'])
    return {'slices': out, 'total': sum(x['revenue'] for x in out)}


def w_scorecard(user, cfg):
    # Each marketplace gets its OWN latest day with sales. A single global
    # latest date drops whichever regions are behind on the clock: USA is
    # hours behind UK/AE/SA, so early in the day it had no revenue yet on the
    # shared date and disappeared from the table entirely.
    DM = _DM(); out = []
    for mp in MARKETPLACES:
        d = (DM.objects.filter(marketplace=mp, revenue__gt=0)
             .aggregate(m=Max('date'))['m'])
        if not d:
            continue
        a = DM.objects.filter(marketplace=mp, date=d).aggregate(
            rev=Sum('revenue'), u=Sum('units'), ppc=Sum('ppc_spend'), gm=Sum('gross_margin'))
        rev = float(a['rev'] or 0)
        if rev <= 0:
            continue
        ppc = max(float(a['ppc'] or 0), _ams_ppc(d, d, {'marketplace': mp}).get((mp, d), 0.0))
        gm = float(a['gm'] or 0)
        out.append({'mp': mp, 'flag': FLAG.get(mp, ''), 'date': d.isoformat(),
                    'revenue': rev, 'units': int(a['u'] or 0),
                    'ppc': ppc, 'tacos': (ppc / rev * 100 if rev else 0),
                    'gm_pct': (gm / rev * 100 if rev else 0), 'net': gm - ppc})
    dates = {r['date'] for r in out}
    return {'date': (out[0]['date'] if len(dates) == 1 and out else None),
            'mixed_dates': len(dates) > 1, 'rows': out}


def w_profit_alerts(user, cfg):
    try:
        from apps.dashboard.models import Alert
    except Exception:
        return {'alerts': []}
    out = []
    for a in Alert.objects.all().order_by('-id')[:8]:
        sev = str(getattr(a, 'severity', None) or getattr(a, 'level', None) or 'info').lower()
        if 'crit' in sev:
            sev = 'crit'
        elif 'warn' in sev:
            sev = 'warn'
        elif 'opp' in sev or 'good' in sev or 'info' in sev:
            sev = 'good'
        out.append({'severity': sev,
                    'title': getattr(a, 'title', None) or getattr(a, 'name', 'Alert'),
                    'body': (getattr(a, 'message', None) or getattr(a, 'description', '') or '')[:140]})
    return {'alerts': out}


def w_walmart_mcf(user, cfg):
    try:
        from apps.walmart_mcf.models import WalmartOrder as W
    except Exception:
        return {'counts': {}}
    counts = {r['status']: r['n'] for r in W.objects.values('status').annotate(n=Count('id'))}
    return {'counts': counts}


def w_data_freshness(user, cfg):
    try:
        from apps.dashboard.models import AdsDataSyncLog as L
    except Exception:
        return {'sources': []}
    today = timezone.localdate(); out = []
    for src in ('sp_hourly', 'orders'):
        d = L.objects.filter(source=src, status='ok').aggregate(m=Max('date'))['m']
        lag = (today - d).days if d else None
        out.append({'source': src, 'latest': d.isoformat() if d else None,
                    'lag': lag, 'ok': (lag is not None and lag <= 2)})
    return {'sources': out}


def w_hourly_heatmap(user, cfg):
    from apps.dashboard.models import HourlyMetricSnapshot as H
    field = 'cm' if any(getattr(f, 'name', '') == 'cm' for f in H._meta.get_fields()) else 'revenue'
    qs = H.objects.all()
    mp = (cfg or {}).get('marketplace')
    qs = qs.filter(marketplace=mp) if mp and mp != 'all' else qs
    d = qs.aggregate(m=Max('date'))['m']
    if not d:
        return {'hours': [0] * 24, 'metric': field, 'date': None}
    qs = qs.filter(date=d)
    per = {r['hour']: float(r['s'] or 0)
           for r in qs.values('hour').annotate(s=Sum(field))}
    # An hour is UNKNOWN — not zero — when it has no snapshot row, and equally
    # when it simply hasn't happened yet in the marketplace's own timezone.
    # The snapshot cron writes rows for the whole day, so a zero for 11pm at
    # 9am local is "not yet", not "no sales". Reporting both as 0 made a
    # part-done day look like a dead one.
    last_hour = 23
    if d == _local_today(mp):
        last_hour = _local_hour(mp)
    hours, gaps = [], 0
    for h in range(24):
        if h > last_hour:                 # hasn't happened yet
            hours.append(None)
        elif h in per:
            hours.append(round(per[h], 2))
        else:                             # elapsed but never synced — a real gap
            hours.append(None); gaps += 1
    return {'hours': hours,
            'elapsed': last_hour + 1, 'covered': (last_hour + 1) - gaps,
            'missing': gaps, 'pending': 23 - last_hour,
            'metric': ('Contribution margin' if field == 'cm' else 'Revenue'),
            'date': d.isoformat()}


def w_top_skus(user, cfg):
    from apps.dashboard.models import DailySkuSnapshot as S
    end = _latest_date(); start = end.replace(day=1)      # month-to-date
    qs = _mp_filter(S.objects.filter(date__range=(start, end)), cfg)
    rows = (qs.values('sku').annotate(cm=Sum('cm'), rev=Sum('revenue'))
            .order_by('-cm')[:6])
    out = [{'sku': r['sku'], 'cm': float(r['cm'] or 0), 'rev': float(r['rev'] or 0)}
           for r in rows if (r['cm'] or 0) > 0]
    top = out[0]['cm'] if out else 1
    for r in out:
        r['pct'] = (r['cm'] / top * 100) if top else 0
    return {'rows': out}


def w_ppc_vs_sales(user, cfg):
    DM = _DM(); end = _latest_date(); start = end - timedelta(days=6)
    rows = (_mp_filter(DM.objects.filter(date__range=(start, end)), cfg)
            .values('date').annotate(rev=Sum('revenue'), ppc=Sum('ppc_spend')).order_by('date'))
    ams = _ams_ppc_by_date(start, end, cfg)
    labels = [r['date'].strftime('%a') for r in rows]
    sales = [float(r['rev'] or 0) for r in rows]
    ppc = [max(float(r['ppc'] or 0), ams.get(r['date'], 0.0)) for r in rows]
    tacos = [round(p / s * 100, 1) if s else 0 for p, s in zip(ppc, sales)]
    return {'labels': labels, 'sales': sales, 'ppc': ppc, 'tacos': tacos}


def w_ai_recs(user, cfg):
    try:
        from apps.dashboard.models import AIRecommendation as R
    except Exception:
        return {'recs': []}
    out = []
    for r in R.objects.all().order_by('-id')[:6]:
        sev = str(getattr(r, 'severity', '') or 'info').lower()
        tag = 'good' if ('opp' in sev or 'info' in sev or 'low' in sev) else \
              ('crit' if 'crit' in sev or 'high' in sev else 'warn')
        out.append({'tag': tag,
                    'text': (getattr(r, 'title', None) or getattr(r, 'recommendation', None)
                             or getattr(r, 'text', 'Recommendation'))[:120]})
    return {'recs': out}


def w_container_timeline(user, cfg):
    from apps.inventory_planning.models import (InTransitShipment as S,
                                                ACTIVE_STATUS_KEYS)
    today = timezone.localdate(); rows = []
    # Only shipments still in flight. Unfiltered, this listed containers
    # received long ago — the widget was showing 2023 ETAs at "-1209d".
    qs = (S.objects
          .filter(status__in=ACTIVE_STATUS_KEYS,
                  received_date__isnull=True, received_at__isnull=True)
          .order_by('eta_destination', 'eta_port'))
    for s in qs[:8]:
        eta = getattr(s, 'eta_destination', None) or getattr(s, 'eta_port', None)
        name = (getattr(s, 'name', None) or getattr(s, 'reference', None)
                or getattr(s, 'container_no', None) or f'Shipment {s.pk}')
        rows.append({'name': str(name)[:28],
                     'eta': eta.isoformat() if eta else None,
                     'days': (eta - today).days if eta else None,
                     'freight': float(getattr(s, 'freight_cost', 0) or 0)})
    return {'shipments': rows}


def w_inventory_risk(user, cfg):
    from apps.inventory_planning.models import WarehouseStock as W
    # `units` is the real column; `detail` is the raw API payload and is
    # frequently empty. Reading only detail made every SKU render "0 left".
    # WarehouseStock is unique per (warehouse, sku), so summing units across
    # warehouses gives total on-hand per SKU.
    # "Risk" means a SKU we are actively selling that is running out. Ranking
    # the whole catalogue by lowest units just surfaces discontinued lines
    # sitting at zero forever — which is why every row read "0 left".
    from apps.dashboard.models import DailySkuSnapshot as D
    end = _latest_date(); start = end - timedelta(days=30)
    selling = set(_mp_filter(D.objects.filter(date__range=(start, end)), cfg)
                  .values_list('sku', flat=True).distinct())
    agg = {}
    for w in W.objects.all().only('sku', 'units', 'detail').iterator(chunk_size=500):
        sku = (w.sku or '').strip()
        if not sku or (selling and sku not in selling):
            continue
        d = w.detail if isinstance(w.detail, dict) else {}
        avail = max(int(w.units or 0),
                    int(d.get('available') or d.get('sellable') or 0))
        agg[sku] = agg.get(sku, 0) + avail
    rows = sorted(({'sku': k, 'available': v} for k, v in agg.items()),
                  key=lambda x: x['available'])[:6]
    return {'rows': rows, 'tracked': len(agg)}


def w_cash_runway(user, cfg):
    from apps.dashboard.models import AmazonPayout as P
    today = timezone.localdate(); start = today - timedelta(days=30)
    qs = _mp_filter(P.objects.filter(payout_date__range=(start, today)), cfg)
    total = float(qs.aggregate(s=Sum('amount'))['s'] or 0)
    recent = [{'date': p.payout_date.isoformat(), 'amount': float(p.amount),
               'mp': p.marketplace}
              for p in _mp_filter(P.objects.all(), cfg).order_by('-payout_date')[:5]]
    return {'total_30d': total, 'recent': recent}


def w_ba_share(user, cfg):
    from apps.sqp.models import SQPSnapshot as Q
    rows = []
    for s in Q.objects.order_by('-period_start', '-search_query_volume')[:6]:
        q = (getattr(s, 'search_query', None)
             or getattr(getattr(s, 'query', None), 'query_text', None)
             or getattr(getattr(s, 'query', None), 'search_query', None) or '')
        share = float(getattr(s, 'purchases_asin_share', 0) or 0) * 100
        rows.append({'query': str(q)[:34], 'share': round(share, 1),
                     'volume': int(getattr(s, 'search_query_volume', 0) or 0)})
    return {'rows': [r for r in rows if r['query']]}


def _placeholder(user, cfg):
    return {'placeholder': True,
            'note': 'Wiring to live data in the next build phase.'}


_PRODUCERS = {
    'kpi': w_kpi, 'revenue_trend': w_revenue_trend, 'marketplace_split': w_marketplace_split,
    'scorecard': w_scorecard, 'profit_alerts': w_profit_alerts,
    'walmart_mcf': w_walmart_mcf, 'data_freshness': w_data_freshness,
    'hourly_heatmap': w_hourly_heatmap, 'top_skus': w_top_skus,
    'ppc_vs_sales': w_ppc_vs_sales, 'ai_recs': w_ai_recs,
    'container_timeline': w_container_timeline, 'inventory_risk': w_inventory_risk,
    'cash_runway': w_cash_runway, 'ba_share': w_ba_share,
}


def get_widget_data(key, user, config=None):
    """Dispatch a widget key → JSON payload. Never raises."""
    if key not in WIDGET_CATALOG:
        return {'error': f'Unknown widget "{key}"'}
    producer = _PRODUCERS.get(key, _placeholder)
    try:
        return producer(user, config or {})
    except Exception as e:      # one bad widget must not break the board
        return {'error': str(e)[:160]}
