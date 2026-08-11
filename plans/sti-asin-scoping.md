# Search Intelligence — ASIN/SKU scoping rework

Status: **PLAN — not executed. For review.**
Author: Claude · 2026-08-07
Supersedes the scoping half of Phase 1 (`plans/search-intelligence-center.md` §3).

**The rule:** everything under Marketing scopes by SKU/ASIN as defined in the
product catalog. Campaign-name terminology is for reporting and display only.

Phase 1 breaks this rule: `ProductGroup.initials` → `Campaign.initials` (parsed
from campaign names) is the current money spine. This plan replaces it.

All figures below: local dev snapshot, window 2026-07-06 → 2026-08-04,
**provisional until re-run on production**.

---

## 1. The evidence — the rule is not just cleaner, it is more correct

`AdsAdvertisedProductDailySnapshot` maps (campaign × ad group × ASIN × day)
from Amazon's own reporting. Joining the search-term table through it, instead
of through parsed campaign names:

| marketplace | campaign-initials route (today) | ASIN route (proposed) |
|---|---|---|
| USA | works — 291 campaigns in the dim | **99.9%** of search-term spend attributable |
| UK | **undercovers** — 74 campaigns in the dim vs 315 advertising | **99.6%** |
| UAE | **0% — no campaign dim rows at all** | **98.6%** |
| KSA | **0% — no campaign dim rows at all** | **95.5%** |

Two findings that change the picture materially:

- **UAE and KSA work under the ASIN route.** They have 2,335 and 2,260
  advertised-product rows despite zero rows in the campaign dimension. Gap
  `MKT-STI-001` (P1) is closed by this change rather than by a sync fix.
- **UK was being silently undercovered** by the current implementation — the
  campaign dimension holds 74 campaigns while 315 actually advertised. Every
  UK number Phase 1 produced is understated. This was not visible before
  because the UK report rendered without complaint.

Catalog mapping is near-total: **96 of 97** advertised ASINs (USA) resolve to a
product group via `Product.category`. Unmapped ASIN spend is **$1**.

Compare with today's data-quality footer: 22 campaigns with no parsed initials,
plus `SD`, `UK` and `Turkish Towel` unmapped. The ASIN route removes that whole
class of problem — a SKU always implies its category, which is an existing
Pulse invariant.

## 2. Grain: ad group, not campaign

Both fact tables carry `ad_group_id`, populated on **every** row (0 blanks in
either table, all four marketplaces). Ad group is the finer and more honest
grain, because that is where ASINs are actually attached.

Purity of ad groups against product groups:

| marketplace | ad groups | single-group | spend in mixed groups |
|---|---|---|---|
| USA | 254 | 243 | **$0 (0.00%)** |
| UK | 73 | 68 | **$0 (0.00%)** |
| UAE | 43 | 38 | $715 (5.10%) |
| KSA | 37 | 36 | $804 (9.16%) |

So in USA and UK the mapping is effectively exact. In UAE and KSA a small
fraction genuinely spans product groups and needs weighting rather than an
all-or-nothing assignment.

## 3. Proposed design

### 3.1 Group membership comes from the catalog only

```
ProductGroup
  categories      ["Bath Towel", "Bath Towel Pack 4", …]   ← the definition
  extra_asins / excluded_asins                              ← manual overrides
  initials        kept, DISPLAY ONLY — never used to scope   ← rule compliance
```

`initials` is not dropped, because campaign naming remains useful for reading a
report. It stops being an input to any query.

### 3.2 Scope resolution becomes a two-step ASIN join

```
group.categories → Product(marketplace).asin/sku          [catalog]
       ↓
AdsAdvertisedProductDailySnapshot [marketplace, window]
       ↓  group spend per (ad_group_id, group) → weight 0-1
ad_group weights → AdsSearchTermDailySnapshot [ad_group_id__in]
       ↓  each term's spend/sales × its ad group's weight
the money spine
```

A weight of 1.0 (the USA/UK norm) makes this identical to a straight filter, so
the weighting costs nothing where it is not needed and stays correct where it
is. Ad groups below a small weight threshold are excluded to avoid noise.

### 3.3 What this does to each Phase 1 module

| module | change | size |
|---|---|---|
| `scope.py` | rewrite `resolve()` around the ASIN join; delete `blank_initials_stats`; rewrite `diagnose_empty` | substantial |
| `spine.py` | filter by `ad_group_id`, apply weights; change margin source (§4.1) | moderate |
| `runner.py` | pass weights through context; new data-quality fields | small |
| `seed_product_groups.py` | seed from categories; initials become descriptive | moderate |
| `models.py` | `ProductGroup.initials` help text + a migration for it | trivial |
| `opportunities.py`, `scoring.py`, `taxonomy.py`, `lexicon.py`, `market.py`, `readiness.py` | **untouched** | none |
| `sti_center.html` | data-quality panel wording only | small |

The intelligence layer — taxonomy, scoring, opportunity generation, the board
and the executive screen — is unaffected. This is a scoping change, not a
redesign.

---

## 4. Internal conflicts — the honest list

### 4.1 CONFLICT (resolvable, and an improvement): the margin source

`spine.margin_rate()` reads `CampaignProfitDaily`, which is **campaign-grained**
— the same terminology the rule pushes away from — and needed a coverage
correction because it counts all revenue against partially-attributed costs
(gap `MKT-STI-003`).

`DailySkuSnapshot` is **per-SKU** and carries `revenue`, `cgs`, `amz_fee`,
`fulfill`, `cm` for all four marketplaces.

**The stored `cm` is already ex-VAT.** `sync.py:356` computes it as
`revenue_net − cgs − amz_fee − fulfill`, where `revenue_net` has already had VAT
extracted. So the invariant is honoured at source and the column can be used
directly — but the **denominator must also be ex-VAT**, or the rate is wrong for
every VAT marketplace:

| marketplace | gross revenue | net (ex-VAT) | CM | rate on gross (**wrong**) | rate ex-VAT (**correct**) |
|---|---|---|---|---|---|
| USA | 214,863 | 214,863 | 61,745 | 28.7% | **28.7%** |
| UK | 21,592 | 17,993 | 6,029 | 27.9% | **33.5%** |
| UAE | 27,431 | 26,125 | 9,859 | 35.9% | **37.7%** |
| KSA | 18,517 | 16,102 | 4,010 | 21.7% | **24.9%** |

Dividing by gross understates UK margin by 5.6 points. USA is unaffected
(`net_factor` 1.0), which is exactly how this class of bug survives review —
it is invisible in the marketplace people look at most.

`cm_rate = Σ cm ÷ (Σ revenue × net_factor(marketplace))`

USA at 28.7% sits close to the 24.0% my coverage correction produced, which
confirms the correction was directionally right — and this source needs no
correction at all.

**Trade-off to accept:** this changes what "margin" means, from *the margin on
ad-attributed sales* to *the margin on the group's whole SKU set*. For pricing
an opportunity that is the better basis, but it is a different number and should
be labelled as such.

**Effect:** closes `MKT-STI-003`, removes the coverage-correction code.

### 4.2 CONFLICT (hard constraint — cannot be fully resolved)

**Amazon does not report search terms per ASIN.** `AdsSearchTermDailySnapshot`
has no ASIN or SKU column, and no Amazon report provides one. A literal
"search terms by ASIN" join does not exist at source.

The proposal therefore routes through ad group → advertised ASIN → catalog.
That satisfies the *intent* of the rule — membership is defined by the catalog,
never by parsing a campaign name — but it is an **attribution**, not a direct
join, and it inherits one assumption: that a search term's spend belongs to the
ASINs its ad group advertised. That assumption is exact for single-ASIN ad
groups and proportional otherwise.

I cannot remove this constraint; I can only be explicit that it exists.

### 4.3 CONFLICT (needs your decision): two attribution mechanisms

`SkuPpcAllocation` already exists and already allocates campaign spend to SKU,
with `attribution_source`, `confidence_score` and `settlement_state`. It is
**campaign-grained** and, in the local snapshot, **USA-only** (555,305 rows; no
UK/AE/SA).

Building ad-group weights inside the Center creates a *second* spend-to-product
attribution in the codebase. That is exactly the failure mode already recorded
in `MKT-ALLOC-002` ("the allocator reads a superseded, campaign-blind copy").
Two mechanisms will drift, and a future reader will not know which is
authoritative.

Options are in §6. This is the one item I would not decide alone.

### 4.4 CONFLICT (cosmetic, unavoidable in Phase 2 scope)

Campaign **names** come from the `Campaign` dimension, which has no UAE/KSA
rows. Once those marketplaces start reporting, the "where it happens" list and
the long-tail waste breakdown will show campaign **IDs** rather than names
there. Nothing else degrades. Fixing it needs the campaign sync extended — the
original `MKT-STI-001` remediation, now demoted from P1 to cosmetic.

### 4.5 Gaps that change

| gap | effect |
|---|---|
| `MKT-STI-001` UAE/KSA cannot be scoped | **closes** — ASIN route covers both |
| `MKT-STI-002` campaigns with no parsed initials | **obsolete** — initials no longer scope anything |
| `MKT-STI-003` margin from partial attribution | **closes** if §4.1 is adopted |
| `MKT-STI-004` stale Brand Analytics | unaffected |
| new | UK undercoverage under the old route — record as closed-on-arrival, or as a note that Phase 1 UK numbers were understated |

### 4.6 No conflict with the rest of Pulse

The rule is already how the rest of the system thinks. "A SKU implies its
category, name and FNSKU" is an existing invariant; `Product.category` is
populated and clean; `SkuPpcAllocation` and `CampaignProfitDaily` both already
attribute to SKU. Phase 1's campaign-initials scoping was the outlier, and
`MKT-ALLOC-001` already records the same complaint about a hardcoded
campaign → product-group map elsewhere in the codebase.

---

## 5. Re-verification required after the change

Every number in the Phase 1 verification was produced under campaign-initials
scoping and must be re-measured:

1. All 64 runs (8 groups × 4 marketplaces × 2 passes) complete, no failures.
2. Spend reconciliation: weighted group spend across all groups ≈ total
   marketplace search-term spend, per marketplace. This is the check that
   proves weighting neither drops nor double-counts money — it was not possible
   under the old route because AE/SA contributed zero.
3. Hand-verify one opportunity's arithmetic against the fact table again.
4. UK totals compared before/after, to size the undercoverage that was there.
5. Confirm UAE/KSA now produce populated reports.
6. Performance: the ASIN join adds a query and a weight pass; confirm the
   sub-second budget holds.

## 5a. Constraint: Financials and Reporting must not change

Confirmed compatible, and verified rather than assumed:

- `sti/` performs **zero writes** to any shared table. Product 24 reads / 0
  writes, DailySkuSnapshot 3/0, Campaign 16/0, search-term 10/0, campaign-profit
  3/0, inventory 3/0, Brand Analytics 6/0. All writes go to the five new tables.
- `models.py` is +263 / −0 — nothing existing altered.
- The planned rework touches only `sti/`, the seed command, the STI template,
  and a help-text-only migration.

**This constraint also settles §4.3.** `SkuPpcAllocation` is the authoritative
PPC cost line in Management P&L — `pnl_engine.py:103` prefers it over the
operational figure — and it also feeds SKU Profitability (`api_pnl_skus`) and
P&L Breakdown (`api_pnl_breakdown`). Extending it would be a change to
Financials. **Option B is ruled out.**

## 5b. Decision: ad-group weights, built as a shared primitive

Adopted: compute `ad_group → ASIN → product group` weights inside `sti/`.

To avoid the divergence `MKT-ALLOC-002` records, the derivation is **not** private
to the scoping code. It lives in one module (`sti/mapping.py`) behind a single
public function returning `{ad_group_id: {group_slug: weight}}`, with no STI
concepts in its signature — so the allocator can adopt it later without
depending on the Center.

Why that matters: Pulse already carries **four** notions of "product group" —
`Campaign.initials` (Phase 1), `Product.title` splitting (hourly aggregator,
catalog, product-type targets), a campaign-name-prefix map for SB/SD
(`MKT-ALLOC-001`), and now `ProductGroup.categories`. A fourth is only
justified if it is built to absorb the others. Catalog-derived membership is
the one that can, because a SKU already implies its category — an existing
Pulse invariant.

## 6. Decisions I need from you

1. **Attribution home** — build ad-group weights inside the Center (fast, ad-group
   grain, all four marketplaces), or extend/reuse `SkuPpcAllocation` (one
   mechanism, but campaign-grained and USA-only today, and a bigger change that
   touches the existing allocator)?
2. **Margin source** — switch to per-SKU actuals from `DailySkuSnapshot`
   (recommended; closes a gap, covers four marketplaces), or keep the
   corrected `CampaignProfitDaily` proxy?
3. **`ProductGroup.initials`** — keep as display-only metadata (recommended), or
   remove the field entirely?
4. **Phase 1 UK numbers** — the stored runs are understated. Delete existing
   `StiReportRun` rows on deploy, or leave them with a caveat?
