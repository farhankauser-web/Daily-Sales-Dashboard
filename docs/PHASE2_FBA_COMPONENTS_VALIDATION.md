# FBA Fee Intelligence — Phase 2 validation record

Status: **VALIDATED, NOT IMPLEMENTED.** Every fact below was observed from live
Amazon responses on the production EC2 environment, not inferred from docs.

---

## A. Source

| | |
|---|---|
| API | Data Kiosk — `POST /dataKiosk/2023-11-15/queries` |
| Schema | `analytics_economics_2024_03_15` |
| Root | `Query.analytics_economics_2024_03_15.economics` |
| Auth | Existing `SPAPIClient._headers()` / `LWATokenManager`. No new mechanism. |
| Role | Selling Partner Insights — already held (200, not 403) |
| History | startDate may be up to **2 years** ago (settlement only reaches 2026-04) |
| Output | JSONL, one record per (date x MSKU) |
| Latency | IN_QUEUE -> DONE in ~25s for a 31-day USA pull (11.9 MB, 30,814 records) |

`GET_SKU_ECONOMICS` **is not a Reports API report type.** Probing it returns
`400 InvalidInput: Invalid Report Type _GET_SKU_ECONOMICS_`. That is a
type-registry rejection, not an authorization failure — no role change fixes it.

### The query that works

```graphql
economics(
  startDate: "YYYY-MM-DD"
  endDate: "YYYY-MM-DD"
  aggregateBy: { date: DAY, productId: MSKU }
  marketplaceIds: ["<marketplace id>"]
  includeComponentsForFeeTypes: [FBA_FULFILLMENT_FEE]
)
```

**`includeComponentsForFeeTypes` is mandatory for Phase 2.** Omit it and
`Fee.components` is null. `FeeType` enum has exactly two members:
`FBA_FULFILLMENT_FEE`, `FBA_STORAGE_FEE`.

---

## B. Component taxonomy — OBSERVED, not assumed

`FeeComponent.name` is a free-text `String!`, **not an enum**. The taxonomy must
be data-driven. Values seen (USA, July 2026, 3,057 fee rows):

```
BaseFbaFulfilmentFee    3057    $292,030.28
FuelSurcharge           2875     $10,418.80
LowInventoryLevelFee     165      $1,354.32
```

Note the British single-`l` **`Fulfilment`**. Settlement uses double-`l`
`FBAPerUnitFulfillmentFee`. A matcher written from the settlement convention
drops 100% of component rows.

### Availability varies by marketplace — render what exists

| Market | Currency | Components present |
|---|---|---|
| USA | USD | Base, Fuel, LowInventoryLevel |
| UK | GBP | Base, Fuel |
| Germany | EUR | Base, Fuel |
| UAE | AED | **Base only** |
| KSA | SAR | **Base only** |

Component names are **not localised** — identical English identifiers in all
five marketplaces including Germany. Do not render a fixed set of bars; a
missing component must be absent, never $0.00.

---

## C. What Amazon does NOT provide

| Spec item | Verdict |
|---|---|
| Dimensional weight / size tier | **Unavailable.** `properties` returned empty on every row despite the schema advertising "Product Size Tier". Must render "Component data unavailable". Do not reverse-engineer. |
| SIPP discount | **Unavailable historically.** `SIPP` exists only as an `ActionType` on `economicsSimulation` (forward-looking what-if). |
| Fuel surcharge *rate* / effective date | Only the per-unit amount is given. No rate, no effective date. |

Per the standing rule: absent data is reported as absent.

---

## D. Two dormant fields

`amountPerUnitDelta` populated on **0 of 3,057 rows**; fee rows split at a
fee-change date: **0**. Both features are real but only fire on an Amazon
fee-change date, and none occurred in July 2026.

**Consequence:** Phase 2 must compute its own period-over-period deltas.
Amazon's `amountPerUnitDelta` is confirmation when present, never the primary
mechanism. Building on it from the schema docs alone would have shipped a page
showing zero drift everywhere.

---

## E. Reconciliation — proven

`Fee.aggregatedDetail.totalAmount` vs SUM(`components[].aggregatedDetail.totalAmount`),
tolerance $0.01, USA July 2026:

```
reconciled     3057  (100.00%)
discrepant        0
no components     0
```

Second identity available from the schema and worth asserting:
`totalAmount = amount - promotionAmount + taxAmount`.

Cross-pull reproducibility: the July portfolio total from the Jun–Aug pull
(**$303,803.40**) equals the component sum from the July-only pull
(292,030.28 + 10,418.80 + 1,354.32) to the penny. Two independent queries.

---

## F. Why Data Kiosk is authoritative (decision record)

Data Kiosk and settlement disagree. Investigated Jun 1 – Aug 15 2026,
SKU `TWL-WHT-BTH-8-600`:

```
June        DK 2,322   settlement 1,049   +121%
July        DK 2,242   settlement 2,586    -13%
Aug 1-15    DK 2,083   settlement 1,592    +31%
FULL        DK 6,647   settlement 5,227    +27%
```

Hypotheses tested and **rejected**:

- *Timing lag alone* — gap WIDENS over the full window and the sign flips
  month to month. Lag scan finds a minimum at 9 days but shallow (2,505
  residual units of 6,647). Real lag, insufficient explanation.
- *Gross vs net units* — only 289 refunded units against a 1,420-unit gap,
  and the direction is wrong (DK is higher overall).

Conclusion: **settlement coverage in Pulse is incomplete and structurally so.**
June is damaged by the `Decimal + float` rollback bug; August is under-covered
because settlements arrive in arrears; July is the one roughly-covered month.

Implication beyond Phase 2: even fully fixed, settlement is ~9 days late and
permanently incomplete at the recent edge — the exact window the drift page
needs most.

**Decision (approved):** Data Kiosk becomes authoritative for FBA fee
intelligence — aggregate and components, all five marketplaces. Settlement is
retained unchanged for P&L cash basis. Monthly variance is surfaced explicitly,
never hidden. Phase 1 figures will be restated onto the more complete basis.

---

## G. Verified example — the commercial finding

`TWL-WHT-BTH-8-600`, USA, July 2026:

```
Jul 1-5    Base ~$7.35 + Fuel $0.27                    = ~$7.65/unit
Jul 6-31   Base ~$7.45 + Fuel $0.27 + LowInv $0.47     = ~$8.20/unit
```

Amazon began charging a **Low Inventory Level Fee on 2026-07-06 at $0.47/unit**
and did not stop. Total for this SKU in July: **~$925**, against a
portfolio-wide low-inventory total of **$1,354** — roughly **68% of the entire
account's low-inventory exposure sits on one SKU**. Fuel was flat at $0.27 all
month and explains none of the movement.

This is Amazon's own attribution, not correlation.

---

## H. Build notes for implementation

1. **No persistent report tracking exists in Pulse.** Report IDs live only in
   in-memory dicts (`_REPORT_INFLIGHT`) and log lines; raw bodies are never
   saved. Spec section 20 needs new infrastructure — there is nothing to reuse.
2. Reuse `SPAPIClient` for auth/endpoint. Add Data Kiosk methods alongside the
   Reports API ones; do not fork the client.
3. Do not replace `SkuFeeActual`. Phase 1 aggregate history stays.
4. Component storage must key on the observed `name` string, not an enum.
5. Query cost is low (~25s for a month of one marketplace) — daily incremental
   ingest with periodic backfill is comfortably affordable.
6. `AggregatedDetail.quantity` is a **Float and nullable** ("may be null or may
   not be an integer" when the fee is not per-unit). Do not cast blindly to int.
7. Marketplace/currency is per row (`Amount.currencyCode`) — never assume USD.

## J. Approved UI design — three panels, driven by two real business problems

The CEO's framing: towels are hand-packed into polybags, so pack dimensions
vary; variable dimensions push units into different size bands and raise the
base fee; and low Amazon stock triggers a separate surcharge. The page must
make both visible and actionable.

### Panel 1 — Packaging Consistency (the polybag problem)

Amazon's base fulfilment fee for a given size tier is a FIXED rate. Therefore
`BaseFbaFulfilmentFee.amountPerUnit` should be FLAT per SKU per day. Observed
for TWL-WHT-BTH-8-600, July 2026: it is not — it ranges $7.0575 to $7.6932,
a 9% swing.

That variance is the fingerprint of units being measured into MULTIPLE size
bands within the same SKU. This is inference from fee behaviour, NOT an
Amazon-stated size tier — the wording must reflect that.

Per SKU show: best rate, weighted-average rate, worst rate, spread, and

    packaging_drift_cost = (avg_base_rate - best_base_rate) x billed_units

Indicative figure for TWL-WHT-BTH-8-600 July: ~$0.37/unit x 2,242 = **~$839**.
Sort by cost. Flat rate = packed consistently. Wide spread = re-check spec.

**PRE-CHECK RESULT — promotions are NOT zero. RULE CHANGED.**

USA July 2026: 140 of 3,057 fee rows carry `promotionAmount`, totalling
**$989.54** (0.33% of the $303,803.40 fee bill). Concentrated on towel SKUs —
e.g. WSH-CLT-WHT-12 $40.20 on a single day.

The schema defines `amountPerUnit` as "final charge amount per unit AFTER
promotion and tax". Promotions therefore DEPRESS it, so a "cheapest day"
benchmark may be cheap due to a discount, not good packing.

    RULE: packaging analysis MUST use  amount / quantity  (gross rate card).
          NEVER use amountPerUnit or totalAmount for size-tier inference.

`AggregatedDetail.amount` is documented as "amount calculated by rate card" —
gross of promotion and tax, so it reflects size tier and nothing else.

ACTION REQUIRED: the validation queries requested gross `amount` only at CHARGE
level, not at COMPONENT level. Add `amount { amount currencyCode }` to the
`components { aggregatedDetail { ... } }` selection before computing anything.

CONFIRMED ON RATE-CARD BASIS (USA, July 2026, SKUs >= 100 units):

    PACKAGING DRIFT = $10,927.62 / month   (~$131k/yr, 3.6% of the fee bill)
    LOW INVENTORY   =  $1,354.32 / month   (~$16k/yr)

    -> Packaging is ~8x the low-inventory problem. Priority accordingly.

Top 10 SKUs = $6,001 = 55% of the drift. Top 20:

    TW-WHTB-BTH-600    6990u  2.8% spread  $1,195.39
    WSH-CLT-WHT-12     7375u  4.1%         $1,070.41
    TW-GRY-KTH-6       5956u  3.6%           $759.12
    TWL-HND-WHT-6      3298u  4.7%           $644.95
    TWL-WHT-BTH-8-600  2242u  6.4%           $539.75
    TW-DK-BTH-4         536u 15.5%           $519.87
    TW-BLK-KTH-6        372u 23.6%           $371.67
    WSH-CLT-DGY-12      955u  9.1%           $349.84
    TWL-LGY-BTH-8-600   564u  8.8%           $278.86
    TW-LG-BTH-4         992u  5.3%           $271.31

The earlier $839 figure for TWL-WHT-BTH-8-600 was NET-basis and overstated by
~55%; the rate-card figure is $539.75. Do not reuse the $839.

TWO DISTINCT PATTERNS — the UI should separate them:
  * High volume / tight spread (2-5%) = SYSTEMATIC. Pack spec sits on a tier
    boundary; a consistent minority of units tips over. Biggest absolute cost.
  * Low volume / wide spread (15-40%) = ERRATIC, and statistically fragile.
    129 units/month is a few per day, so one odd unit swings the average.

METHODOLOGY CAVEAT: the benchmark is "best observed DAY", and each day is
itself a units-weighted average. High-volume SKUs therefore UNDERSTATE drift
(averaging masks the true correctly-packed rate); low-volume SKUs are noisy in
both directions. Apply a minimum-units gate and label low-confidence rows.

PREFERRED BENCHMARK FOR THE BUILD: use `economicsPreview` with
`feeTypes: [BASE_FBA_FULFILLMENT_FEE]` to obtain Amazon's EXPECTED base fee for
the SKU as listed, then drift = actual_rate_card - expected. That is an
authoritative benchmark rather than an inferred one. Keep observed-minimum only
as a fallback where preview returns nothing.

SIDE BENEFIT: $989.54/month of fee promotions is a real saving and should
appear in the waterfall as a credit line. It partially fills the slot left
empty by SIPP being unavailable.

### Panel 2 — Low Inventory Exposure

Directly stated by Amazon, no inference. Per affected SKU: first charge date,
per-unit amount, units affected, total cost, with `days_cover` from
`InventorySnapshot` overlaid on the same timeline.

Language rule: "Amazon charged a Low Inventory Level Fee from <date>" is fact.
"Cover fell below X on <date>; the fee first appeared on <date>" is sequence.
Never assert that inventory planning CAUSED the fee.

Observed: TWL-WHT-BTH-8-600 began 2026-07-06 at $0.47/unit, ~$925 in July —
about 68% of the entire account's $1,354 low-inventory total.

### Panel 3 — Forward-looking prevention (highest value)

`economicsPreview(feeTypes: [LOW_INVENTORY_LEVEL_FEE, BASE_FBA_FULFILLMENT_FEE],
aggregateBy: {date: RANGE, productId: MSKU})` returns expected fees up to
**120 days ahead**. This flags SKUs ABOUT TO be charged, while restocking can
still prevent it. Turns the page from post-mortem into prevention.

Preview also gives an expected base rate per SKU — a benchmark to compare
billed base rates against, strengthening Panel 1 beyond a bare spread.

### Cross-system link

The low-inventory fee is a known, quantified per-unit penalty for running lean
($0.47/unit observed). It belongs in the reorder-point / safety-stock maths in
`inventory_planning` alongside stockout cost — otherwise the true cost of a
lean position is understated.

### Possible enhancement (UNVERIFIED — probe before promising)

Amazon's FBA fee-preview report is believed to carry catalogue size tier and
dimensions per SKU. If so, cross-referencing it against billed base rates would
allow "listing says Large Standard, 30% of units billed higher". Not yet
probed. Do not design around it until confirmed.

---

## K. DATA HAZARDS — observed live, must be handled by the ingest

Found while proving the client against USA 2026-08-01..07 (6,958 rows,
784 with fees, 685 charges with components).

### K1. EXCLUDE Amazon-generated `amzn.` MSKUs — BUSINESS RULE

Amazon returns generated MSKUs alongside real ones:

    TWL-WHT-BTH-8-600                              <- real seller SKU
    amzn.gr.TW-BLU-BTH-4-iTlG9GVkiK8EKmva-LN       <- Amazon-generated
    amzn.gr.LUX-PK2-TWL-TEL-qyCObLH5LN9JG-VG
    amzn.gr.BTH-SHT-WHT-600-u72fnT-Qj6l-r-LN

CONFIRMED BY THE BUSINESS (CEO): these are **customer-returned units that
Amazon relists itself** under grade-and-resell as "Used - Like New" or similar
conditions. They are a DIFFERENT ECONOMIC EVENT from new-unit fulfilment.

    RULE: exclude any msku beginning with `amzn.` from ALL FBA fee impact
          analysis — packaging drift, fee drift, low-inventory, waterfall.

Rationale: a returned unit's handling has nothing to do with how the factory
filled the polybag, so including them corrupts packaging attribution.

This REPLACES the earlier plan to resolve them via ASIN. No string surgery, no
ASIN fallback needed for these — they are simply filtered out. (Plain `msku`
-> `Product.sku` matching still applies for real SKUs.)

Store them if useful for a separate returns view, but never in fee impact.

IMPACT OF THE RULE — MEASURED, July 2026 USA (2,837 real / 220 amzn charges):

                            REAL SKUs      grade-and-resell
    BaseFbaFulfilmentFee   $290,040.00        $1,990.28
    FuelSurcharge           $10,346.92           ~$71.88
    LowInventoryLevelFee     $1,354.32            $0.00
                           ------------      -----------
                           $301,741.24        ~$2,062.16

Both sides reconcile back to the pre-filter total of $303,803.40 — the filter
is clean, nothing is lost or double-counted.

Grade-and-resell is only 0.68% of the fee bill. CONSEQUENCES:

  * LowInventoryLevelFee was ALREADY entirely on real SKUs. The $1,354.32/month
    figure needs NO adjustment.
  * The $10,927.62 packaging-drift total STANDS. It gated on >=100 units/month
    per SKU, and 220 charges spread across many distinct `amzn.` SKUs means
    none could clear that bar.
  * The top-20 SKU list contains no `amzn.` entries; the top-10 action list
    stands unchanged.

The rule still matters going forward — grade-and-resell volume can grow, and
per-SKU packaging attribution must never mix returned units with new ones.

### K2. Zero-quantity and negative-amount adjustment rows

    BaseFbaFulfilmentFee   gross = -0.26   qty = 0.0
    FuelSurcharge          gross = +0.26   qty = 1.0

NOTE: every observed instance of this occurred on an `amzn.` grade-and-resell
SKU, so the K1 exclusion removes most of it. Keep the guards anyway — defensive,
cheap, and not proven to be exclusive to those SKUs.

Amazon reclassifying an amount between components; nets to zero. Therefore:

  * `quantity` CAN BE 0.0 -> guard every division.
  * `amount` CAN BE NEGATIVE -> per-unit rates must handle sign.
  * These rows are REAL and must be STORED (they affect totals) but must be
    EXCLUDED from rate-per-unit derivation, or the packaging-drift figure is
    polluted by meaningless negative "rates".

Suggested rule: store all rows; derive rate-card rate only where
`quantity > 0 AND amount > 0`; flag the rest as `adjustment`.

### K3. Most rows carry no fees

784 of 6,958 rows (11%) had any fees; the rest are zero-sale days. The ingest
should skip empty-fee rows rather than writing empty component records.

### K4. Volume / latency

A 7-day USA pull returned 6,958 rows in seconds; 31 days returned 30,814 rows
(11.9 MB) in ~25s. Daily incremental ingest is cheap — no elaborate windowing
needed. LowInventoryLevelFee was still active in Aug 1-7 (44 occurrences).

---

## I. Outstanding

- Confirm June settlement patchiness via `SkuFeeActual` monthly density query
  (read-only, gathers evidence for the restatement).
- Settlement `amount-description` histogram for AE/SA/DE — no longer blocks fee
  intelligence, but `SettlementLineActual` still feeds P&L.
- `normalize_product_titles --apply` on EC2 (178 titles, 6 need manual review).
- 6 DE settlement reports failing on 429 — backoff gives up too early.
