"""
apps/dashboard/pnl_lines.py — canonical Management P&L line definitions.

This is the single source of truth for the statement structure. The schema,
the auto-feed engine, the manual-entry form, the Excel importer, and the
renderer all read this list so they stay in lockstep.

Mirrors the client's "P&L Summary" Excel exactly (Infinitee LLC Statement of
Operations). Each line has:

    key       stable identifier (snake_case) — used as DB key, never shown
    label     display text (matches the Excel label verbatim)
    section   grouping header it lives under
    source    'auto'      → filled from settlement / COGS / PPC
              'manual'    → typed in (form) or imported (Excel), regional ccy
              'computed'  → derived from other lines (formula in pnl_engine)
              'header'    → section title row, no value
              'metric'    → below-the-line KPI (units, ARPU, per-unit fees)
    sign      '+' adds to the running total, '-' subtracts, '' = n/a
    indent    UI indent level (0 = section, 1 = line, 2 = sub-line)

The `source='auto'` lines additionally carry a `feed` key naming which
settlement bucket / engine field supplies them (see pnl_engine.AUTO_FEED).
"""
from __future__ import annotations

# Section keys
SEC_REVENUE   = 'revenue'
SEC_COGS      = 'cogs'
SEC_AMZ_FEES  = 'amazon_fees'
SEC_MARKETING = 'marketing'
SEC_OTHER_INC = 'other_income'
SEC_GM        = 'gross_margin'
SEC_STORAGE   = 'storage'
SEC_OPEX      = 'operating_expenses'
SEC_HR        = 'human_resource'
SEC_NET       = 'net'
SEC_METRICS   = 'metrics'


# Each tuple: (key, label, section, source, sign, indent, feed)
# feed is only meaningful for source='auto'.
PNL_LINES: list[dict] = [
    # ── REVENUE ──────────────────────────────────────────────────────────
    dict(key='sales_amazon',        label='Sales - Amazon',
         section=SEC_REVENUE, source='auto',     sign='+', indent=1, feed='gross_sales'),
    dict(key='vat',                 label='VAT',
         section=SEC_REVENUE, source='auto',     sign='-', indent=1, feed='vat'),
    dict(key='sales_return',        label='Sales Return',
         section=SEC_REVENUE, source='auto',     sign='-', indent=1, feed='returns'),
    dict(key='promotional_discounts', label='Promotional Discounts',
         section=SEC_REVENUE, source='auto',     sign='-', indent=1, feed='promo'),
    dict(key='net_sales',           label='Net Sales',
         section=SEC_REVENUE, source='computed', sign='',  indent=0, feed=None),
    dict(key='sales_return_pct',    label='Sales Return %',
         section=SEC_REVENUE, source='computed', sign='',  indent=1, feed=None),

    # ── COST OF SALES ────────────────────────────────────────────────────
    dict(key='cost_of_sales',       label='Cost of Sales',
         section=SEC_COGS, source='auto',     sign='-', indent=0, feed='cogs'),
    dict(key='cost_of_sales_pct',   label='Cost of Sales %',
         section=SEC_COGS, source='computed', sign='',  indent=1, feed=None),

    # ── AMAZON / RETAIL FEES ─────────────────────────────────────────────
    dict(key='amazon_commission',   label='Amazon Commission',
         section=SEC_AMZ_FEES, source='auto',   sign='-', indent=1, feed='commission'),
    dict(key='amazon_fulfilment_fee', label='Amazon- Fulfilment fee',
         section=SEC_AMZ_FEES, source='auto',   sign='-', indent=1, feed='fba_fee'),
    dict(key='walmart_retail_commission', label='Wallmart Commision / Retail Commission',
         section=SEC_AMZ_FEES, source='manual', sign='-', indent=1, feed=None),

    # ── MARKETING EXPENSES ───────────────────────────────────────────────
    dict(key='_hdr_marketing',      label='Marketing Expenses',
         section=SEC_MARKETING, source='header', sign='',  indent=0, feed=None),
    dict(key='amazon_ppc',          label='Amazon PPC',
         section=SEC_MARKETING, source='auto',   sign='-', indent=1, feed='ppc'),
    dict(key='promo_other_platforms', label='Promotion on other Platforms',
         section=SEC_MARKETING, source='manual', sign='-', indent=1, feed=None),
    dict(key='sampling_cost',       label='Sampling Cost',
         section=SEC_MARKETING, source='manual', sign='-', indent=1, feed=None),
    dict(key='other_marketing',     label='Other Marketing Expenses',
         section=SEC_MARKETING, source='manual', sign='-', indent=1, feed=None),

    # ── OTHER INCOME ─────────────────────────────────────────────────────
    dict(key='other_income',        label='Other Income',
         section=SEC_OTHER_INC, source='auto',   sign='+', indent=0, feed='other_income'),

    # ── GROSS MARGIN ─────────────────────────────────────────────────────
    dict(key='gross_margin',        label='Gross Margin',
         section=SEC_GM, source='computed', sign='', indent=0, feed=None),
    dict(key='gross_margin_pct',    label='Gross Margin %',
         section=SEC_GM, source='computed', sign='', indent=1, feed=None),

    # ── STORAGE COST ─────────────────────────────────────────────────────
    dict(key='_hdr_storage',        label='Storage Cost',
         section=SEC_STORAGE, source='header', sign='',  indent=0, feed=None),
    dict(key='warehouse_rent',      label='Warehouse Rent',
         section=SEC_STORAGE, source='manual', sign='-', indent=1, feed=None),
    dict(key='awd_transportation',  label='AWD Transportation',
         section=SEC_STORAGE, source='auto',   sign='-', indent=2, feed='awd_transportation'),
    dict(key='awd_processing',      label='AWD Processing',
         section=SEC_STORAGE, source='auto',   sign='-', indent=2, feed='awd_processing'),
    dict(key='awd_storage',         label='AWD Storage',
         section=SEC_STORAGE, source='auto',   sign='-', indent=2, feed='awd_storage'),
    dict(key='fba_inventory_storage_fee', label='FBA Inventory Storage Fee',
         section=SEC_STORAGE, source='auto',   sign='-', indent=1, feed='storage_fee'),
    dict(key='inbound_transportation', label='Inbound Transportation',
         section=SEC_STORAGE, source='auto',   sign='-', indent=1, feed='inbound_transportation'),
    dict(key='other_logistics_fees', label='Other Inventory/Logistics Fees',
         section=SEC_STORAGE, source='auto',   sign='-', indent=1, feed='other_logistics'),

    # ── OPERATING EXPENSES (Fixed Cost) ──────────────────────────────────
    dict(key='_hdr_opex',           label='Operating Expenses',
         section=SEC_OPEX, source='header', sign='', indent=0, feed=None),
    dict(key='amazon_subscription_fee', label='Amazon Subscription Fee',
         section=SEC_OPEX, source='auto',   sign='-', indent=1, feed='subscription'),
    dict(key='entertainment',       label='Entertainment',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='travel_accommodation', label='Travelling & Accomodation',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='corporate_giveaways', label='Corporate Giveaways',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='employees_training',  label='Employees Training',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='trademark_legal_fee', label='Trademark/Legal Fee',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='inspection_charges_3p', label='3P Inspection Charges',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='courier_charges',     label='Courier Charges',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='mobile_laptops',      label='Mobile/Laptops',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='it_expense',          label='IT Expense (Email, website hosting & redesign)',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='software_system_charges', label='Software and System Charges',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='virtual_office_rent', label='Virtual Office Rent',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='tax_consultancy_charges', label='Tax consultancy charges',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='audit_charges',       label='Audit Charges (Review)',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='product_photography', label='Product Photography and Videos',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='bank_charges',        label='Bank Charges',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='trucking_cost',       label='Trucking Cost',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='amazon_account_management', label='Amazon Account Management',
         section=SEC_OPEX, source='auto', sign='-', indent=1, feed='account_management'),
    dict(key='other_costs',         label='Others Costs',
         section=SEC_OPEX, source='manual', sign='-', indent=1, feed=None),
    dict(key='total_operating_expenses', label='Total Operating Expenses',
         section=SEC_OPEX, source='computed', sign='', indent=0, feed=None),

    # ── HUMAN RESOURCE COST ──────────────────────────────────────────────
    dict(key='_hdr_hr',             label='Human Resource Cost',
         section=SEC_HR, source='header', sign='', indent=0, feed=None),
    dict(key='hr_pakistan_dedicated', label='HR Cost (Pakistan) Dedicated',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='hr_pakistan_new_hiring', label='HR Cost (Pakistan) Dedicated- New hirring',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='hr_pakistan_shared',  label='HR Cost (Pakistan) Shared',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='hr_uae',              label='HR Cost (UAE)',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='hr_shared_staff_uae', label='HR Cost (Shared Staff UAE)',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='consultancy_bpo_usa', label='Consultancy Charges BPO (USA)',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='rushmore',            label='Rushmore',
         section=SEC_HR, source='manual', sign='-', indent=1, feed=None),
    dict(key='total_hr_cost',       label='Total Human Resource Cost',
         section=SEC_HR, source='computed', sign='', indent=0, feed=None),

    # ── NET ──────────────────────────────────────────────────────────────
    dict(key='net_profit_before_tax', label='Net Profit/(Loss) before Tax',
         section=SEC_NET, source='computed', sign='', indent=0, feed=None),
    dict(key='net_margin_before_tax_pct', label='Net Margin before Tax %',
         section=SEC_NET, source='computed', sign='', indent=1, feed=None),
    dict(key='tax_expense',         label='Tax Expense (State Tax)',
         section=SEC_NET, source='manual',   sign='-', indent=1, feed=None),
    dict(key='net_profit_after_tax', label='Net Profit/(Loss) after Tax',
         section=SEC_NET, source='computed', sign='', indent=0, feed=None),
    dict(key='net_margin_after_tax_pct', label='Net Margin after Tax %',
         section=SEC_NET, source='computed', sign='', indent=1, feed=None),

    # ── METRICS (below the line) ─────────────────────────────────────────
    dict(key='total_units_sold',    label='Total Units Sold',
         section=SEC_METRICS, source='auto',     sign='', indent=1, feed='units_sold'),
    dict(key='inventory_returned',  label='Inventory Returned by Customers',
         section=SEC_METRICS, source='auto',     sign='', indent=1, feed='units_returned'),
    dict(key='net_units',           label='Net Units Sold',
         section=SEC_METRICS, source='computed', sign='', indent=1, feed=None),
    dict(key='arpu',                label='ARPU',
         section=SEC_METRICS, source='computed', sign='', indent=1, feed=None),
    dict(key='per_unit_cogs',       label='Per Unit Cost of Sales',
         section=SEC_METRICS, source='computed', sign='', indent=1, feed=None),
    dict(key='per_unit_commission', label='Fee - Amazon Selling Commission',
         section=SEC_METRICS, source='computed', sign='', indent=1, feed=None),
    dict(key='per_unit_fulfilment', label='Fee - Amazon Shipping and Handling',
         section=SEC_METRICS, source='computed', sign='', indent=1, feed=None),
]


SECTION_LABELS = {
    SEC_REVENUE:   'Revenue',
    SEC_COGS:      'Cost of Sales',
    SEC_AMZ_FEES:  'Selling Fees',
    SEC_MARKETING: 'Marketing Expenses',
    SEC_OTHER_INC: 'Other Income',
    SEC_GM:        'Gross Margin',
    SEC_STORAGE:   'Storage Cost',
    SEC_OPEX:      'Operating Expenses',
    SEC_HR:        'Human Resource Cost',
    SEC_NET:       'Net Result',
    SEC_METRICS:   'Operating Metrics',
}


# Fast lookups
LINE_BY_KEY: dict[str, dict] = {ln['key']: ln for ln in PNL_LINES}
MANUAL_KEYS: list[str] = [ln['key'] for ln in PNL_LINES if ln['source'] == 'manual']
AUTO_KEYS:   list[str] = [ln['key'] for ln in PNL_LINES if ln['source'] == 'auto']


def label_to_key(label: str) -> str | None:
    """Map an Excel label (loose match) back to a line key — for the importer."""
    norm = ' '.join((label or '').lower().split())
    for ln in PNL_LINES:
        if ' '.join(ln['label'].lower().split()) == norm:
            return ln['key']
    return None
