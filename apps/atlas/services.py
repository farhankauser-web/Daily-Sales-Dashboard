"""
Atlas Phase 1 services: kg-rate pricing, quotation building with automatic
revision snapshots, stock red-flags, and the funnel aggregates.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (AtlasCustomer, AtlasProduct, NegativeStockLog,
                     Quotation, QuotationLine, QuotationRevision)


def price_product(customer: AtlasCustomer, product: AtlasProduct,
                  order_type: str) -> dict:
    """
    SOW pricing:
        weight_kg  = L × W (cm) / 10,000 / 1,000 × GSM
        unit_price = kg_rate × weight_kg
    kg_rate: per-article override → customer flat rate → 0 (needs setup).
    """
    weight = product.weight_kg
    rate = customer.kg_rate_for(order_type, product) or Decimal('0')
    unit = (rate * weight).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return {'kg_rate': rate, 'weight_kg': weight, 'unit_price': unit,
            'cost': product.cost or Decimal('0'),
            'margin_unit': unit - (product.cost or 0)}


def next_reference(company) -> str:
    year = timezone.now().year
    prefix = f'ATL-{company.code.upper()}-{year}-'
    last = (Quotation.objects.filter(reference__startswith=prefix)
            .order_by('-reference').values_list('reference', flat=True).first())
    n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'{prefix}{n:04d}'


def _snapshot(q: Quotation) -> dict:
    t = q.totals()
    return {
        'reference': q.reference, 'status': q.status,
        'customer': q.customer.name, 'order_type': q.order_type,
        'container_type': q.container_type,
        'payment_term': q.payment_term.name if q.payment_term else None,
        'discount_pct': str(q.discount_pct), 'discount_amount': str(q.discount_amount),
        'expected_delivery': str(q.expected_delivery or ''),
        'remarks': q.remarks,
        'lines': [{'sku': ln.product.sku, 'qty': ln.quantity,
                   'kg_rate': str(ln.kg_rate), 'weight_kg': str(ln.weight_kg),
                   'unit_price': str(ln.unit_price), 'cost': str(ln.cost),
                   'discount_pct': str(ln.discount_pct)}
                  for ln in q.lines.select_related('product')],
        'totals': {k: str(v) for k, v in t.items()},
    }


def write_revision(q: Quotation, user=None, note: str = '') -> None:
    last = q.revisions.order_by('-number').values_list('number', flat=True).first()
    QuotationRevision.objects.create(
        quotation=q, number=(last or 0) + 1,
        snapshot=_snapshot(q), change_note=note[:300], changed_by=user)


@transaction.atomic
def create_quotation(company, customer: AtlasCustomer, order_type: str,
                     items: list[dict], user=None, *,
                     container_type: str = '', payment_term=None,
                     discount_pct=0, discount_amount=0,
                     expected_delivery=None, remarks: str = '',
                     is_sample_request: bool = False) -> Quotation:
    """
    items: [{product_id, quantity, discount_pct?, unit_price? (override)}]
    Prices auto-populate from the customer's kg rates; stock shortages are
    red-flagged and logged (quotes are never blocked by stock — SOW §18/26).
    """
    q = Quotation.objects.create(
        company=company, reference=next_reference(company),
        customer=customer, order_type=order_type,
        container_type=container_type if order_type == 'container' else '',
        payment_term=payment_term or customer.default_payment_term,
        discount_pct=Decimal(str(discount_pct or 0)),
        discount_amount=Decimal(str(discount_amount or 0)),
        expected_delivery=expected_delivery, remarks=remarks,
        is_sample_request=is_sample_request, created_by=user)

    shortage = False
    for it in items:
        product = AtlasProduct.objects.get(pk=it['product_id'],
                                           company=company)
        qty = int(it.get('quantity') or 1)
        p = price_product(customer, product, order_type)
        unit = (Decimal(str(it['unit_price']))
                if it.get('unit_price') not in (None, '')
                else p['unit_price'])
        short = max(qty - max(product.stock_qty, 0), 0)
        QuotationLine.objects.create(
            quotation=q, product=product, quantity=qty,
            kg_rate=p['kg_rate'], weight_kg=p['weight_kg'],
            unit_price=unit, cost=p['cost'],
            discount_pct=it.get('discount_pct') or 0,
            stock_short=short)
        if short:
            shortage = True
            NegativeStockLog.objects.create(
                company=company, quotation=q, product=product,
                qty_short=short)
    if shortage:
        q.has_stock_shortage = True
        q.save(update_fields=['has_stock_shortage'])
    write_revision(q, user, 'created')
    return q


def set_status(q: Quotation, status: str, user=None, *,
               lost_reason: str = '', note: str = '') -> Quotation:
    q.status = status
    if status == 'sent' and not q.sent_at:
        q.sent_at = timezone.now()
    if status in ('won', 'lost'):
        q.decided_at = timezone.now()
    if status == 'lost':
        q.lost_reason = lost_reason[:200]
    q.save()
    write_revision(q, user, note or f'status → {status}')
    return q


def funnel(company, *, date_from=None, date_to=None, customer_id=None,
           order_type='', quality='', sku='') -> dict:
    """Sent/won/lost counts + values with the SOW's filter set."""
    qs = Quotation.objects.filter(company=company)
    if date_from:   qs = qs.filter(created_at__date__gte=date_from)
    if date_to:     qs = qs.filter(created_at__date__lte=date_to)
    if customer_id: qs = qs.filter(customer_id=customer_id)
    if order_type:  qs = qs.filter(order_type=order_type)
    if quality:     qs = qs.filter(lines__product__quality__icontains=quality)
    if sku:         qs = qs.filter(lines__product__sku__icontains=sku)
    qs = qs.distinct()

    out = {s: {'count': 0, 'value': Decimal('0')} for s, _ in
           [('draft', ''), ('sent', ''), ('won', ''), ('lost', '')]}
    for q in qs.prefetch_related('lines', 'lines__product', 'company', 'customer'):
        out[q.status]['count'] += 1
        out[q.status]['value'] += q.totals()['net']
    sent_all = out['sent']['count'] + out['won']['count'] + out['lost']['count']
    decided = out['won']['count'] + out['lost']['count']
    out['win_rate'] = round(out['won']['count'] / decided * 100, 1) if decided else 0.0
    out['sent_total'] = sent_all
    return out


# ── Phase 3: Finance & Billing ──────────────────────────────────────────────

def next_invoice_number(company) -> str:
    from datetime import date
    from .models import AtlasInvoice
    prefix = f'INV-{company.code.upper()}-{date.today():%Y}-'
    last = (AtlasInvoice.objects.filter(number__startswith=prefix)
            .order_by('-number').first())
    seq = (int(last.number.rsplit('-', 1)[1]) + 1) if last else 1
    return f'{prefix}{seq:04d}'


def invoice_from_quotation(quotation, invoice_date=None, user=None):
    """Raise a draft invoice from a quotation, copying its priced lines and
    totals, due date from the payment term."""
    from datetime import date, timedelta
    from decimal import Decimal
    from django.db import transaction
    from .models import AtlasInvoice, AtlasInvoiceLine

    with transaction.atomic():
        t = quotation.totals()
        term = quotation.payment_term
        idate = invoice_date or date.today()
        due = idate + timedelta(days=term.days) if term else None
        inv = AtlasInvoice.objects.create(
            company=quotation.company, customer=quotation.customer,
            quotation=quotation, number=next_invoice_number(quotation.company),
            invoice_date=idate, due_date=due, payment_term=term,
            currency=quotation.company.currency,
            subtotal=t['subtotal'], discount=t['discount'], vat=t['vat'],
            total=t['total'], status='draft', created_by=user)
        for ln in quotation.lines.all():
            AtlasInvoiceLine.objects.create(
                invoice=inv, product=ln.product,
                description=(ln.product.description if ln.product
                            else 'Item'),
                quantity=ln.quantity, unit_price=ln.unit_price,
                line_total=ln.line_total)
        return inv


def ar_aging(company):
    """Accounts-receivable aging buckets for a company's open invoices."""
    from datetime import date
    from .models import AtlasInvoice
    today = date.today()
    buckets = {'current': 0.0, 'd30': 0.0, 'd60': 0.0, 'd90': 0.0, 'd90p': 0.0}
    by_customer: dict = {}
    total_out = 0.0
    for inv in (AtlasInvoice.objects.filter(company=company)
                .exclude(status__in=['paid', 'void'])
                .select_related('customer').prefetch_related('payments')):
        bal = float(inv.balance)
        if bal <= 0:
            continue
        total_out += bal
        overdue_days = (today - inv.due_date).days if inv.due_date else 0
        if overdue_days <= 0:
            b = 'current'
        elif overdue_days <= 30:
            b = 'd30'
        elif overdue_days <= 60:
            b = 'd60'
        elif overdue_days <= 90:
            b = 'd90'
        else:
            b = 'd90p'
        buckets[b] += bal
        cust = by_customer.setdefault(inv.customer.name,
                                      {'name': inv.customer.name, 'total': 0.0,
                                       'current': 0.0, 'd30': 0.0, 'd60': 0.0,
                                       'd90': 0.0, 'd90p': 0.0})
        cust['total'] += bal
        cust[b] += bal
    rows = sorted(by_customer.values(), key=lambda c: -c['total'])
    return {'buckets': {k: round(v, 2) for k, v in buckets.items()},
            'total': round(total_out, 2), 'customers': rows}
