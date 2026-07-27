"""Atlas Phase 1 pages: customers, products, quotations + funnel."""
from __future__ import annotations

import json
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.decorators import permission_required

from . import services
from .models import (ORDER_TYPES, AtlasCompany, AtlasCustomer, AtlasProduct,
                     PaymentTerm, Quotation)


def _company(request) -> AtlasCompany:
    code = request.GET.get('co') or request.POST.get('co') or ''
    qs = AtlasCompany.objects.filter(is_active=True)
    return qs.filter(code=code).first() or qs.order_by('code').first()


def _base_ctx(request, company):
    return {'company': company,
            'companies': AtlasCompany.objects.filter(is_active=True).order_by('code')}


# ── Customers ────────────────────────────────────────────────────────────────

@login_required
@permission_required('can_view_dashboard')
def customers(request, pk=None):
    company = _company(request)
    instance = get_object_or_404(AtlasCustomer, pk=pk, company=company) if pk else None

    if request.method == 'POST':
        d = request.POST
        obj = instance or AtlasCustomer(company=company)
        obj.name = d.get('name', '').strip()
        obj.contact_person = d.get('contact_person', '').strip()
        obj.email = d.get('email', '').strip()
        obj.phone = d.get('phone', '').strip()
        obj.address = d.get('address', '').strip()
        obj.trn = d.get('trn', '').strip()
        obj.kg_rate_local = d.get('kg_rate_local') or None
        obj.kg_rate_container = d.get('kg_rate_container') or None
        obj.default_payment_term_id = d.get('default_payment_term') or None
        obj.terms_conditions = d.get('terms_conditions', '')
        obj.notes = d.get('notes', '')
        obj.is_active = d.get('is_active') == 'on'
        if not obj.name:
            messages.error(request, 'Customer name is required.')
        else:
            obj.save()
            messages.success(request, f'Customer "{obj.name}" saved.')
            return redirect(f"{request.path.rsplit('/edit', 1)[0].rsplit('/', 1)[0] if pk else request.path}?co={company.code}"
                            if pk else f'{request.path}?co={company.code}')

    ctx = _base_ctx(request, company)
    ctx.update({
        'rows': (AtlasCustomer.objects.filter(company=company)
                 .select_related('default_payment_term').order_by('name')),
        'terms': PaymentTerm.objects.filter(is_active=True),
        'edit': instance,
    })
    return render(request, 'atlas/customers.html', ctx)


# ── Products ────────────────────────────────────────────────────────────────

@login_required
@permission_required('can_view_dashboard')
def products(request, pk=None):
    company = _company(request)
    instance = get_object_or_404(AtlasProduct, pk=pk, company=company) if pk else None

    if request.method == 'POST':
        d = request.POST
        obj = instance or AtlasProduct(company=company)
        try:
            obj.sku = d.get('sku', '').strip().upper()
            obj.description = d.get('description', '').strip()
            obj.length_cm = d.get('length_cm') or 0
            obj.width_cm = d.get('width_cm') or 0
            obj.gsm = int(d.get('gsm') or 0)
            obj.yarn = d.get('yarn', '').strip()
            obj.size_label = d.get('size_label', '').strip()
            obj.colour = d.get('colour', '').strip()
            obj.dyeing = d.get('dyeing', '')
            obj.structure = d.get('structure', '')
            obj.quality = d.get('quality', '').strip()
            obj.cost = d.get('cost') or 0
            obj.stock_qty = int(d.get('stock_qty') or 0)
            obj.is_active = d.get('is_active') == 'on'
            if not obj.sku:
                raise ValueError('SKU is required')
            obj.save()
            messages.success(request, f'Product {obj.sku} saved '
                             f'(weight {obj.weight_kg:.5f} kg).')
            return redirect(f'/atlas/products/?co={company.code}')
        except Exception as exc:
            messages.error(request, f'Save failed: {exc}')

    ctx = _base_ctx(request, company)
    ctx.update({
        'rows': AtlasProduct.objects.filter(company=company).order_by('sku'),
        'edit': instance,
        'qualities': (AtlasProduct.objects.filter(company=company)
                      .exclude(quality='').values_list('quality', flat=True)
                      .distinct()),
    })
    return render(request, 'atlas/products.html', ctx)


# ── Quotations ──────────────────────────────────────────────────────────────

@login_required
@permission_required('can_view_dashboard')
def quotations(request):
    company = _company(request)
    f = {k: request.GET.get(k, '') for k in
         ('status', 'customer', 'order_type', 'quality', 'sku', 'from', 'to')}

    qs = (Quotation.objects.filter(company=company)
          .select_related('customer', 'payment_term')
          .prefetch_related('lines', 'lines__product'))
    if f['status']:     qs = qs.filter(status=f['status'])
    if f['customer']:   qs = qs.filter(customer_id=f['customer'])
    if f['order_type']: qs = qs.filter(order_type=f['order_type'])
    if f['quality']:    qs = qs.filter(lines__product__quality__icontains=f['quality'])
    if f['sku']:        qs = qs.filter(lines__product__sku__icontains=f['sku'])
    if f['from']:       qs = qs.filter(created_at__date__gte=f['from'])
    if f['to']:         qs = qs.filter(created_at__date__lte=f['to'])
    qs = qs.distinct()[:300]

    fun = services.funnel(
        company,
        date_from=f['from'] or None, date_to=f['to'] or None,
        customer_id=f['customer'] or None, order_type=f['order_type'],
        quality=f['quality'], sku=f['sku'])

    rows = []
    from django.utils import timezone
    now = timezone.now()
    for q in qs:
        t = q.totals()
        age = (now - q.created_at).days
        rows.append({'q': q, 'totals': t, 'age_days': age})

    ctx = _base_ctx(request, company)
    ctx.update({'rows': rows, 'funnel': fun, 'f': f,
                'customers': AtlasCustomer.objects.filter(
                    company=company, is_active=True).order_by('name')})
    return render(request, 'atlas/quotations.html', ctx)


@login_required
@permission_required('can_view_dashboard')
def quotation_new(request):
    company = _company(request)
    if request.method == 'POST':
        try:
            d = request.POST
            items = json.loads(d.get('items_json') or '[]')
            if not items:
                raise ValueError('Add at least one product line.')
            exp = d.get('expected_delivery') or None
            q = services.create_quotation(
                company,
                AtlasCustomer.objects.get(pk=d['customer'], company=company),
                d.get('order_type', 'local'), items, request.user,
                container_type=d.get('container_type', ''),
                payment_term=(PaymentTerm.objects.filter(pk=d.get('payment_term'))
                              .first()),
                discount_pct=d.get('discount_pct') or 0,
                discount_amount=d.get('discount_amount') or 0,
                expected_delivery=(datetime.strptime(exp, '%Y-%m-%d').date()
                                   if exp else None),
                remarks=d.get('remarks', ''),
                is_sample_request=d.get('is_sample_request') == 'on')
            if q.has_stock_shortage:
                messages.warning(request, f'{q.reference} created with a '
                                 f'STOCK SHORTAGE flag — see red lines.')
            else:
                messages.success(request, f'{q.reference} created.')
            return redirect(f'/atlas/quotations/{q.pk}/?co={company.code}')
        except Exception as exc:
            messages.error(request, f'Could not create quotation: {exc}')

    ctx = _base_ctx(request, company)
    ctx.update({
        'customers': AtlasCustomer.objects.filter(company=company,
                                                  is_active=True).order_by('name'),
        'terms': PaymentTerm.objects.filter(is_active=True),
        'qualities': sorted(set(
            AtlasProduct.objects.filter(company=company, is_active=True)
            .exclude(quality='').values_list('quality', flat=True))),
    })
    return render(request, 'atlas/quotation_new.html', ctx)


@login_required
@permission_required('can_view_dashboard')
def api_products_priced(request):
    """Products (optionally by quality) with live price preview for the
    selected customer + order type — feeds the quotation wizard."""
    company = _company(request)
    quality = request.GET.get('quality', '')
    order_type = request.GET.get('order_type', 'local')
    customer = (AtlasCustomer.objects
                .filter(pk=request.GET.get('customer'), company=company).first())
    qs = AtlasProduct.objects.filter(company=company, is_active=True)
    if quality:
        qs = qs.filter(quality=quality)
    out = []
    for p in qs.order_by('sku')[:500]:
        priced = (services.price_product(customer, p, order_type)
                  if customer else None)
        out.append({
            'id': p.pk, 'sku': p.sku, 'description': p.description,
            'quality': p.quality, 'gsm': p.gsm,
            'size': f'{p.length_cm}×{p.width_cm}cm',
            'colour': p.colour, 'stock': p.stock_qty,
            'weight_kg': float(p.weight_kg),
            'kg_rate': float(priced['kg_rate']) if priced else None,
            'unit_price': float(priced['unit_price']) if priced else None,
            'cost': float(p.cost or 0),
        })
    return JsonResponse({'products': out,
                         'currency': company.currency})


@login_required
@permission_required('can_view_dashboard')
def quotation_detail(request, pk):
    company = _company(request)
    q = get_object_or_404(
        Quotation.objects.select_related('customer', 'payment_term', 'company')
        .prefetch_related('lines', 'lines__product', 'revisions'),
        pk=pk, company=company)
    ctx = _base_ctx(request, company)
    ctx.update({'q': q, 'totals': q.totals(),
                'revisions': q.revisions.order_by('-number')})
    return render(request, 'atlas/quotation_detail.html', ctx)


@login_required
@permission_required('can_view_dashboard')
@require_POST
def quotation_status(request, pk):
    company = _company(request)
    q = get_object_or_404(Quotation, pk=pk, company=company)
    status = request.POST.get('status')
    if status not in ('sent', 'won', 'lost', 'draft'):
        messages.error(request, 'Invalid status.')
    else:
        services.set_status(q, status, request.user,
                            lost_reason=request.POST.get('lost_reason', ''),
                            note=request.POST.get('note', ''))
        messages.success(request, f'{q.reference} → {status.upper()}')
    return redirect(f'/atlas/quotations/{q.pk}/?co={company.code}')


# ═════════════════════════════ PHASE 2 ══════════════════════════════════════

from datetime import date as _date  # noqa: E402

from . import supply  # noqa: E402
from .models import (AtlasSupplier, Backorder, PurchaseOrder,  # noqa: E402
                     RFQ, RFQResponse)


@login_required
@permission_required('can_view_dashboard')
def rfqs(request):
    company = _company(request)

    if request.method == 'POST':
        d = request.POST
        action = d.get('action')
        try:
            if action == 'create':
                supply.create_rfq(
                    company, request.user,
                    customer_id=d.get('customer') or None,
                    product_id=d.get('product') or None,
                    description=d.get('description', ''),
                    size=d.get('size', ''), gsm=int(d['gsm']) if d.get('gsm') else None,
                    construction=d.get('construction', ''),
                    quantity=int(d.get('quantity') or 0),
                    port=d.get('port', ''),
                    attachment=request.FILES.get('attachment'))
                messages.success(request, 'RFQ created — supply chain has 24h to respond.')
            elif action == 'respond':
                rfq = get_object_or_404(RFQ, pk=d['rfq_id'], company=company)
                supply.respond_rfq(
                    rfq, request.user, kind=d.get('kind', 'actual'),
                    manufacturer_construction=d.get('manufacturer_construction', ''),
                    moq=int(d['moq']) if d.get('moq') else None,
                    lead_time_days=int(d['lead_time_days']) if d.get('lead_time_days') else None,
                    fob_rate=d.get('fob_rate') or None,
                    cnf_rate=d.get('cnf_rate') or None,
                    ddp_rate=d.get('ddp_rate') or None,
                    vendor_kg_rate=d.get('vendor_kg_rate') or None,
                    inquiry_date=d.get('inquiry_date') or None,
                    request_date=d.get('request_date') or None,
                    rates_received_date=d.get('rates_received_date') or None,
                    remarks=d.get('remarks', ''))
                messages.success(request, f'Response recorded for {rfq.reference}.')
            elif action == 'revalidate':
                rfq = get_object_or_404(RFQ, pk=d['rfq_id'], company=company)
                supply.request_revalidation(rfq)
                messages.success(request, f'Revalidation requested for {rfq.reference} (24h TAT).')
            elif action == 'apply_cost':
                resp = get_object_or_404(RFQResponse, pk=d['response_id'],
                                         rfq__company=company)
                supply.apply_response_to_cost(resp, d.get('rate_field', 'fob'))
                messages.success(
                    request, f'{resp.rfq.product.sku} cost updated from '
                             f'{d.get("rate_field", "fob").upper()} rate — '
                             f'prices/margins now use it.')
        except Exception as exc:
            messages.error(request, f'RFQ action failed: {exc}')
        return redirect(f'/atlas/rfqs/?co={company.code}')

    rows = (RFQ.objects.filter(company=company)
            .select_related('customer', 'product')
            .prefetch_related('responses')[:200])
    ctx = _base_ctx(request, company)
    ctx.update({
        'rows': rows,
        'overdue_count': sum(1 for r in rows if r.is_overdue),
        'customers': AtlasCustomer.objects.filter(company=company, is_active=True),
        'products_list': AtlasProduct.objects.filter(company=company, is_active=True),
    })
    return render(request, 'atlas/rfqs.html', ctx)


@login_required
@permission_required('can_view_dashboard')
def purchase_orders(request):
    company = _company(request)

    if request.method == 'POST':
        d = request.POST
        action = d.get('action')
        try:
            if action == 'create':
                items = json.loads(d.get('items_json') or '[]')
                if not items:
                    raise ValueError('Add at least one line.')
                supplier = None
                if d.get('supplier_name', '').strip():
                    supplier, _ = AtlasSupplier.objects.get_or_create(
                        company=company, name=d['supplier_name'].strip())
                customer = (AtlasCustomer.objects
                            .filter(pk=d.get('customer'), company=company)
                            .first())
                po = supply.create_po(
                    company, items, request.user, supplier=supplier,
                    customer=customer, notes=d.get('notes', ''))
                messages.success(request, f'{po.reference} created with tracking stages.')
            elif action == 'advance':
                po = get_object_or_404(PurchaseOrder, pk=d['po_id'], company=company)
                nxt = supply.complete_stage(po)
                messages.success(request, f'{po.reference}: stage completed'
                                 + (f' → now "{nxt.name}"' if nxt else ' — all stages done'))
            elif action == 'receive':
                po = get_object_or_404(PurchaseOrder, pk=d['po_id'], company=company)
                received = {}
                for ln in po.lines.all():
                    v = d.get(f'recv_{ln.pk}')
                    if v:
                        received[ln.pk] = int(v)
                res = supply.receive_po(po, received)
                msg = f'{po.reference} receipt recorded.'
                if res['backorders_created']:
                    msg += f' {res["backorders_created"]} backorder(s) created.'
                if res['all_received']:
                    msg += ' PO fully received.'
                messages.success(request, msg)
            elif action == 'backorder':
                bo = get_object_or_404(Backorder, pk=d['bo_id'], company=company)
                supply.resolve_backorder(bo, d.get('resolution', 'received'))
                messages.success(request, f'Backorder {bo.pk} → {d.get("resolution")}.')
        except Exception as exc:
            messages.error(request, f'PO action failed: {exc}')
        return redirect(f'/atlas/purchase-orders/?co={company.code}')

    pos = (PurchaseOrder.objects.filter(company=company)
           .select_related('supplier', 'customer')
           .prefetch_related('lines', 'lines__product', 'stages',
                             'backorders', 'backorders__product')[:100])
    ctx = _base_ctx(request, company)
    ctx.update({
        'rows': pos,
        'breaches': sum(1 for p in pos for s in p.stages.all() if s.is_overdue),
        'open_backorders': Backorder.objects.filter(
            company=company, status='open').select_related('product', 'customer'),
        'customers': AtlasCustomer.objects.filter(company=company, is_active=True),
        'products_list': AtlasProduct.objects.filter(company=company, is_active=True),
    })
    return render(request, 'atlas/purchase_orders.html', ctx)


@login_required
@permission_required('can_view_dashboard')
def forecast(request):
    company = _company(request)
    if request.method == 'POST':
        d = request.POST
        try:
            p = get_object_or_404(AtlasProduct, pk=d['product_id'], company=company)
            p.sell_through_daily = d.get('sell_through_daily') or 0
            p.production_lead_days = int(d.get('production_lead_days') or 0)
            p.shipment_lead_days = int(d.get('shipment_lead_days') or 0)
            p.is_peak = d.get('is_peak') == 'on'
            p.peak_multiplier = d.get('peak_multiplier') or 1
            p.save()
            messages.success(request, f'{p.sku} forecasting inputs saved.')
        except Exception as exc:
            messages.error(request, f'Save failed: {exc}')
        return redirect(f'/atlas/forecast/?co={company.code}')

    rows = []
    for p in AtlasProduct.objects.filter(company=company, is_active=True).order_by('sku'):
        rows.append({'p': p, 'f': supply.forecast_product(p)})
    due = sum(1 for r in rows if r['f']['refill_due'])
    ctx = _base_ctx(request, company)
    ctx.update({'rows': rows, 'due_count': due})
    return render(request, 'atlas/forecast.html', ctx)


# ── Phase 3: Finance & Billing ──────────────────────────────────────────────

@login_required
@permission_required('can_view_dashboard')
def invoices(request):
    from datetime import date
    from .models import AtlasInvoice, Quotation
    company = _company(request)
    invs = (AtlasInvoice.objects.filter(company=company)
            .select_related('customer').prefetch_related('payments'))
    rows = []
    kpi = {'outstanding': 0.0, 'overdue': 0.0, 'paid_mtd': 0.0, 'count': 0}
    for i in invs:
        bal = float(i.balance)
        rows.append({'id': i.pk, 'number': i.number, 'customer': i.customer.name,
                     'date': i.invoice_date, 'due': i.due_date,
                     'total': float(i.total), 'paid': float(i.paid),
                     'balance': bal, 'status': i.status,
                     'overdue': i.is_overdue})
        kpi['count'] += 1
        if i.status not in ('paid', 'void'):
            kpi['outstanding'] += bal
            if i.is_overdue:
                kpi['overdue'] += bal
    # quotations that can still be invoiced (won, not yet invoiced)
    won = (Quotation.objects.filter(company=company, status='won',
                                    invoices__isnull=True)
           .select_related('customer'))
    ctx = _base_ctx(request, company)
    ctx.update({'rows': rows, 'kpi': kpi,
                'invoiceable': [{'id': q.pk, 'ref': q.reference,
                                 'customer': q.customer.name,
                                 'total': float(q.totals()['total'])}
                                for q in won]})
    return render(request, 'atlas/invoices.html', ctx)


@login_required
@permission_required('can_manage_cogs')
def invoice_detail(request, pk):
    from datetime import date, datetime
    from decimal import Decimal
    from .models import AtlasInvoice, AtlasPayment
    company = _company(request)
    inv = get_object_or_404(AtlasInvoice.objects.prefetch_related(
        'lines', 'payments'), pk=pk, company=company)

    if request.method == 'POST':
        act = request.POST.get('action')
        if act == 'pay':
            amt = Decimal(request.POST.get('amount') or 0)
            if amt > 0:
                try:
                    pdate = datetime.strptime(request.POST.get('date', ''),
                                              '%Y-%m-%d').date()
                except ValueError:
                    pdate = date.today()
                AtlasPayment.objects.create(
                    invoice=inv, date=pdate, amount=amt,
                    method=request.POST.get('method', 'bank'),
                    reference=request.POST.get('reference', '')[:64])
                inv.recompute_status()
                inv.save(update_fields=['status'])
        elif act == 'send' and inv.status == 'draft':
            inv.status = 'sent'
            inv.save(update_fields=['status'])
        elif act == 'void':
            inv.status = 'void'
            inv.save(update_fields=['status'])
        return redirect(f'{request.path}?co={company.code}')

    ctx = _base_ctx(request, company)
    ctx.update({'inv': inv, 'lines': inv.lines.all(),
                'payments': inv.payments.all(),
                'today': date.today().isoformat()})
    return render(request, 'atlas/invoice_detail.html', ctx)


@login_required
@permission_required('can_manage_cogs')
@require_POST
def invoice_create(request):
    from .models import Quotation
    company = _company(request)
    q = get_object_or_404(Quotation, pk=request.POST.get('quotation'),
                          company=company)
    from .services import invoice_from_quotation
    inv = invoice_from_quotation(q, user=request.user)
    return redirect(f"{reverse('atlas:invoice_detail', args=[inv.pk])}"
                    f"?co={company.code}")


@login_required
@permission_required('can_view_dashboard')
def ar_aging_page(request):
    from .services import ar_aging
    company = _company(request)
    ctx = _base_ctx(request, company)
    ctx.update(ar_aging(company))
    return render(request, 'atlas/ar_aging.html', ctx)
