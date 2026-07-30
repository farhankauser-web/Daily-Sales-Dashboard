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

# Sensible default for a brand-new user (x,y in 12-col grid).
DEFAULT_LAYOUT = [
    {'key': 'kpi', 'x': 0, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'revenue'}},
    {'key': 'kpi', 'x': 3, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'gross_margin'}},
    {'key': 'kpi', 'x': 6, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'tacos'}},
    {'key': 'kpi', 'x': 9, 'y': 0, 'w': 3, 'h': 2, 'config': {'metric': 'units'}},
    {'key': 'revenue_trend', 'x': 0, 'y': 2, 'w': 8, 'h': 3},
    {'key': 'marketplace_split', 'x': 8, 'y': 2, 'w': 4, 'h': 3},
    {'key': 'profit_alerts', 'x': 0, 'y': 5, 'w': 4, 'h': 4},
    {'key': 'scorecard', 'x': 4, 'y': 5, 'w': 8, 'h': 3},
    {'key': 'walmart_mcf', 'x': 4, 'y': 8, 'w': 4, 'h': 2},
    {'key': 'data_freshness', 'x': 8, 'y': 8, 'w': 4, 'h': 2},
]


# ── helpers ─────────────────────────────────────────────────────────────────
def _DM():
    from apps.dashboard.models import DailyMetric
    return DailyMetric


def _latest_date():
    d = _DM().objects.aggregate(m=Max('date'))['m']
    return d or timezone.localdate()


def _mp_filter(qs, cfg):
    mp = (cfg or {}).get('marketplace')
    return qs.filter(marketplace=mp) if mp and mp != 'all' else qs


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
        spark = [round(float(x['p'] or 0) / float(x['r']) * 100, 2) if x['r'] else 0 for x in rows]
        cur = spark[-1] if spark else 0
        prev = sum(spark[:-1]) / max(len(spark) - 1, 1) if len(spark) > 1 else cur
        return {'label': 'TACoS · latest day', 'value': cur, 'format': 'pct',
                'delta': (cur - prev), 'delta_unit': 'pt', 'delta_good': 'down',
                'delta_label': 'vs 14d avg', 'spark': spark}
    m = _METRICS.get(metric, _METRICS['revenue']); f = m['field']
    rows = (_mp_filter(DM.objects.filter(date__range=(start, d)), cfg)
            .values('date').annotate(s=Sum(f)).order_by('date'))
    spark = [float(x['s'] or 0) for x in rows]
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
    DM = _DM(); d = _latest_date(); out = []
    for mp in MARKETPLACES:
        a = DM.objects.filter(marketplace=mp, date=d).aggregate(
            rev=Sum('revenue'), u=Sum('units'), ppc=Sum('ppc_spend'), gm=Sum('gross_margin'))
        rev = float(a['rev'] or 0)
        if rev <= 0:
            continue
        ppc = float(a['ppc'] or 0); gm = float(a['gm'] or 0)
        out.append({'mp': mp, 'flag': FLAG.get(mp, ''), 'revenue': rev, 'units': int(a['u'] or 0),
                    'ppc': ppc, 'tacos': (ppc / rev * 100 if rev else 0),
                    'gm_pct': (gm / rev * 100 if rev else 0), 'net': gm - ppc})
    return {'date': d.isoformat(), 'rows': out}


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


def _placeholder(user, cfg):
    return {'placeholder': True,
            'note': 'Wiring to live data in the next build phase.'}


_PRODUCERS = {
    'kpi': w_kpi, 'revenue_trend': w_revenue_trend, 'marketplace_split': w_marketplace_split,
    'scorecard': w_scorecard, 'profit_alerts': w_profit_alerts,
    'walmart_mcf': w_walmart_mcf, 'data_freshness': w_data_freshness,
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
