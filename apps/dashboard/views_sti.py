"""
apps/dashboard/views_sti.py — Search Intelligence Center.

The page renders a STORED RUN. Generation is an explicit POST, because the
pipeline joins a multi-million-row fact table with weekly Brand Analytics data
and a scoring pass — seconds of work, which is fine on a button press and not
fine on every page load. Everything the template needs is inside
`StiReportRun.payload`, so rendering an old run costs one row read.
"""
import csv
import json
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.decorators import permission_required

from .forms import ProductGroupForm
from .models import Product, ProductGroup, StiOpportunity, StiReportRun
from .sti import config as cfg
from .sti import narrative as narrative_mod
from .sti import outcomes as outcomes_mod
from .sti import periods as periods_mod
from .sti import runner
from .views import _allowed_marketplaces


def _parse_date(raw):
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


@login_required
@permission_required('can_view_dashboard')
def search_intelligence(request):
    """Page shell — renders the selected run, or an empty state with the picker."""
    allowed = _allowed_marketplaces(request.user)
    marketplace = request.GET.get('mp') or allowed[0]
    if not request.user.can_access_marketplace(marketplace):
        marketplace = allowed[0]

    groups = list(ProductGroup.objects.filter(active=True))
    group_slug = request.GET.get('group') or (groups[0].slug if groups else '')
    group = next((g for g in groups if g.slug == group_slug), None)

    run = None
    if request.GET.get('run'):
        run = StiReportRun.objects.filter(pk=request.GET['run']).first()
    elif group:
        qs = StiReportRun.objects.filter(product_group=group,
                                         marketplace=marketplace, status='complete')
        if request.GET.get('period'):
            qs = qs.filter(period_key=request.GET['period'])
        run = qs.first()

    history = []
    if group:
        history = list(StiReportRun.objects
                       .filter(product_group=group, marketplace=marketplace)
                       .values('id', 'date_from', 'date_to', 'status',
                               'generated_at', 'duration_ms')[:15])

    # Opportunity status lives on the model, not in the frozen payload, so a
    # run viewed later reflects what the team has since acted on.
    statuses = {}
    if run and run.payload:
        ids = [o.get('id') for o in run.payload.get('opportunities', []) if o.get('id')]
        statuses = dict(StiOpportunity.objects.filter(id__in=ids)
                        .values_list('id', 'status'))

    # The selector offers Amazon reporting periods, marked with what data each
    # one has. Anchoring strictly to Brand Analytics would guarantee market data
    # on every report and would also leave UK, UAE and KSA with nothing to pick,
    # since Brand Analytics covers USA alone today.
    ptype = request.GET.get('ptype', periods_mod.WEEKLY)
    if ptype not in dict(periods_mod.PERIOD_TYPES):
        ptype = periods_mod.WEEKLY
    asins = []
    if group:
        from .models import Product
        asins = list(Product.objects.filter(marketplace=marketplace,
                                            category__in=group.categories)
                     .values_list('asin', flat=True))
    period_options = periods_mod.available(marketplace, ptype, asins)
    selected_key = request.GET.get('period') or (run.period_key if run else '')
    if not any(p.key == selected_key for p in period_options):
        d = periods_mod.default_period(period_options)
        selected_key = d.key if d else ''

    ctx = {
        'marketplace':          marketplace,
        'allowed_marketplaces': allowed,
        'groups':               groups,
        'group':                group,
        'group_slug':           group_slug,
        'period_types':         periods_mod.PERIOD_TYPES,
        'ptype':                ptype,
        'period_options':       period_options,
        'selected_period':      selected_key,
        'run':                  run,
        'payload':              run.payload if (run and run.status == 'complete') else None,
        'payload_json':         json.dumps(run.payload if (run and run.status == 'complete')
                                           else {}),
        'statuses_json':        json.dumps(statuses),
        'history':              history,
        'opp_statuses':         StiOpportunity.STATUS,
    }
    return render(request, 'dashboard/sti_center.html', ctx)


@login_required
@permission_required('can_view_dashboard')
def sti_generate(request):
    """POST → run the pipeline, then redirect to the finished run."""
    if request.method != 'POST':
        return redirect('dashboard:search_intelligence')

    marketplace = request.POST.get('mp', 'usa')
    if not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)

    group = get_object_or_404(ProductGroup, slug=request.POST.get('group'))
    ptype = request.POST.get('ptype', periods_mod.WEEKLY)
    from .models import Product
    asins = list(Product.objects.filter(marketplace=marketplace,
                                        category__in=group.categories)
                 .values_list('asin', flat=True))
    period = runner.resolve_period(request.POST.get('period'), marketplace, ptype, asins)
    if period is None:
        return redirect(f"{reverse('dashboard:search_intelligence')}"
                        f"?group={group.slug}&mp={marketplace}")

    run = runner.generate(group, marketplace, period, user=request.user)

    url = (f"{reverse('dashboard:search_intelligence')}"
           f"?group={group.slug}&mp={marketplace}&ptype={ptype}"
           f"&period={period.key}&run={run.id}")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'run_id': run.id, 'status': run.status,
                             'error': run.error, 'redirect': url})
    return redirect(url)


@login_required
@permission_required('can_view_dashboard')
def sti_opportunity_status(request, pk):
    """Mark an opportunity in progress / done / dismissed."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method'}, status=405)

    opp = get_object_or_404(StiOpportunity, pk=pk)
    if not request.user.can_access_marketplace(opp.marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)

    status = (request.POST.get('status') or '').strip()
    if status not in dict(StiOpportunity.STATUS):
        return JsonResponse({'error': 'bad status'}, status=400)

    opp.status = status
    opp.status_note = request.POST.get('note', '')[:2000]
    fields = ['status', 'status_note', 'updated_at']
    if status == 'done' and not opp.acted_period_key:
        # Anchor the action to a period so its effect can be measured against
        # the following one.
        opp.acted_period_key = request.POST.get('period_key', '')[:16]
        fields.append('acted_period_key')
    opp.save(update_fields=fields)
    return JsonResponse({'ok': True, 'id': opp.id, 'status': opp.status})


@login_required
@permission_required('can_view_dashboard')
def sti_outcomes(request):
    """
    The scoreboard: did acting on these opportunities actually work?

    Recommending is easy and every keyword tool does it. Checking afterwards is
    what this module can do and they cannot, because it holds the history and
    the margins. Only opportunities marked done AND anchored to a period can be
    measured — the anchor is what makes "before" and "after" mean the same
    number of days on the same grid.
    """
    allowed = _allowed_marketplaces(request.user)
    marketplace = request.GET.get('mp') or ''
    if marketplace and not request.user.can_access_marketplace(marketplace):
        marketplace = ''

    group = None
    if request.GET.get('group'):
        group = ProductGroup.objects.filter(slug=request.GET['group']).first()

    board = outcomes_mod.scoreboard(marketplace=marketplace, group=group)

    if request.GET.get('export') == 'csv':
        return _outcomes_csv(board, marketplace)

    ctx = {
        'marketplace':          marketplace,
        'allowed_marketplaces': allowed,
        'groups':               list(ProductGroup.objects.filter(active=True)),
        'group':                group,
        'group_slug':           group.slug if group else '',
        'board':                board,
    }
    return render(request, 'dashboard/sti_outcomes.html', ctx)


def _outcomes_csv(board, marketplace):
    """
    Export for checking the numbers outside Pulse.

    Every column the verdict rests on travels with it — the metric, the
    direction that counts as success, both periods and both values — so the
    judgement can be re-derived in a spreadsheet rather than taken on trust.
    """
    resp = HttpResponse(content_type='text/csv')
    stamp = date.today().isoformat()
    name = f'sti-outcomes-{marketplace or "all"}-{stamp}.csv'
    resp['Content-Disposition'] = f'attachment; filename="{name}"'

    w = csv.writer(resp)
    w.writerow(['Marketplace', 'Product group', 'Opportunity type', 'Title', 'Subject',
                'Metric', 'Success when', 'Acted period', 'Result period',
                'Before', 'After', 'Change %', 'Verdict',
                'Value at action (CM/month)', 'Note'])
    for r in board['rows']:
        w.writerow([
            r.marketplace.upper(), r.group, r.opp_type, r.title, r.subject,
            r.metric_label, r.good_when, r.acted_period, r.result_period,
            '' if r.before is None else round(r.before, 4),
            '' if r.after is None else round(r.after, 4),
            '' if r.delta_pct is None else r.delta_pct,
            r.verdict, r.score_at_action, r.note,
        ])
    return resp


def _catalog_categories() -> list:
    return sorted(c for c in Product.objects.order_by()
                  .values_list('category', flat=True).distinct() if c)


@login_required
@permission_required('can_view_dashboard')
def sti_groups(request):
    """
    Curate the product groups every report is scoped to.

    The page leads with what is NOT covered, because that is the only part that
    needs a decision: a catalog category in no group is spend and demand no
    report can see, and an unseen leak is the expensive kind.
    """
    groups = list(ProductGroup.objects.all())
    all_cats = _catalog_categories()
    claimed = {c for g in groups for c in (g.categories or [])}
    unmapped = [c for c in all_cats if c not in claimed]

    rows = []
    for g in groups:
        counts = {}
        for mp in ['usa', 'uk', 'ae', 'sa']:
            n = Product.objects.filter(marketplace=mp,
                                       category__in=g.categories or []).count()
            if n:
                counts[mp] = n
        rows.append({'group': g, 'counts': counts,
                     'asins': sum(counts.values()),
                     'categories': len(g.categories or [])})

    return render(request, 'dashboard/sti_groups.html', {
        'rows': rows,
        'unmapped': unmapped,
        'unmapped_products': Product.objects.filter(category__in=unmapped).count()
                             if unmapped else 0,
        'total_categories': len(all_cats),
    })


@login_required
@permission_required('can_view_dashboard')
def sti_group_form(request, pk=None):
    """Create or edit one product group."""
    instance = get_object_or_404(ProductGroup, pk=pk) if pk else None
    counts = {c: n for c, n in Product.objects.order_by().values_list('category')
              .annotate(n=Count('id')) if c}
    choices = [(c, f'{c}  ({counts.get(c, 0)})') for c in _catalog_categories()]
    form = ProductGroupForm(request.POST or None, instance=instance,
                            category_choices=choices)

    if request.method == 'POST' and form.is_valid():
        g = form.save()
        messages.success(request, f'Product group "{g.name}" saved.')
        return redirect('dashboard:sti_groups')

    return render(request, 'dashboard/sti_group_form.html', {
        'form': form, 'instance': instance,
    })


@login_required
@permission_required('can_view_dashboard')
def sti_narrate(request, pk):
    """
    Ask the narrator to explain a stored run.

    On demand rather than on generation: it costs money and latency, and the
    report is complete and usable without it (`MKT-D-014` — AI explains, it is
    never the source of truth).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'method'}, status=405)

    run = get_object_or_404(StiReportRun, pk=pk)
    if not request.user.can_access_marketplace(run.marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)

    result = narrative_mod.generate(run)

    # Cached on the run so re-opening the report does not re-bill the call. The
    # narrative belongs to the run it described, not to the reader.
    if result['ok']:
        run.payload['narrative'] = {
            'text': result['text'], 'warnings': result['warnings'],
            'model': result.get('model', ''),
        }
        run.save(update_fields=['payload'])
    return JsonResponse(result)
