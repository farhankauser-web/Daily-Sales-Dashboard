# Marketing

Amazon advertising: what we spend, what it returns, and which SKU it returned it
for. Five nav items, two independent data pipelines, and the only place in Pulse
where a cost has to be attributed rather than simply recorded.

## Purpose

This section owns **PPC** — Sponsored Products, Brands and Display. Campaign
performance, search terms, placements, the spend-to-SKU attribution that makes
per-SKU profit possible, and the two pipelines that feed all of it.

It does not own organic search performance. Search-query share, market share and
baskets belong to **[brand-analytics](../brand-analytics/README.md)**. The distinction is worth
holding onto: this section is *paid*, that one is *earned*.

It does not own the profit those campaigns contribute to. Margin, COGS and the
P&L belong to **[financials](../financials/README.md)**. This section supplies the ad cost;
that one decides what it means.

**This section is complete and frozen** except for future feature changes. Seven
features are documented; the registers are the backlog, not unwritten work. The
process lessons are in [RETROSPECTIVE.md](RETROSPECTIVE.md).

## Features

| Document | Covers | Open here when |
|---|---|---|
| [ads-api.md](ads-api.md) | the settled daily reports — submit, poll, download | a report never arrives, or arrives empty |
| [ams-stream.md](ams-stream.md) | S3 + Firehose → hourly campaign figures | hourly data is missing or a subscription fails |
| [hourly-upload.md](hourly-upload.md) | Seller Central hourly CSV → the same table | an uploaded day disagrees with the stream |
| [sku-allocation.md](sku-allocation.md) | campaign spend → SKU | per-SKU ad cost or TACoS looks wrong |
| [campaigns.md](campaigns.md) | campaign centre, profit, detail tabs | a campaign's figures look wrong |
| [search-terms.md](search-terms.md) | search-term performance and summaries | a term's spend or conversion looks wrong |
| [placements.md](placements.md) | placement performance | placement multipliers or splits look wrong |

## Relationships

```
Ads API  ──daily reports──→ campaign · targeting · search term · placement rows
                                    ↓                    ↓
AMS stream  ──hourly──┐             ↓          campaign profit (nightly)
                      ├→ hourly figures              ↓        ↓
Hourly upload ────────┘             ↓        search terms   placements
                                    ↓
                        SKU allocation: spend → SKU
                                    ↓
                    per-SKU ad cost → TACoS, CM%, the SKU table
```

Three facts about this shape cause most confusion:

- **Two pipelines, different grains and different latencies.** The Ads API
  delivers complete daily reports a day or two late. The AMS stream delivers
  hourly records continuously. They are not alternatives — the daily report is
  the settled figure and the stream is the early signal.
- **Attribution is a decision, not a fact.** Amazon reports spend per campaign,
  not per SKU. Everything per-SKU in Pulse — ad cost, TACoS, contribution
  margin — rests on the allocation this section performs.
- **There are two attribution paths, and they are not rivals.** Campaign profit
  uses Amazon's own advertised-product rows; the SKU allocator spreads whole
  campaign budgets across SKUs including campaigns Amazon attributes nothing to.
  One answers "what did this campaign earn", the other "what did this SKU cost".
  They read the same source through different tables today — `ARCH-009`.

## Ground truth

Established before any document was written, and unchanged by them. *Source:
local development data; provisional against production.*

**The laptop runs no scheduled jobs — by design.** Production runs all 33 jobs in
`deploy/crontab.txt` continuously; this machine runs none and is not always on.
Every freshness difference between Marketing tables reflects which command
someone last ran by hand. **Staleness here is never evidence of a defect**, and
neither is an empty or partly filled table.

What the local data is good for is confirming the machinery executes, and it
does: the allocator runs correctly on demand, the stream has consumed 9,052 S3
objects, and the detail-report pipeline resolved every day but two across three
months and nine report kinds.

Marketplaces carrying advertising data: `usa` and `uk`.

## Navigation

| Working on… | Load |
|---|---|
| per-SKU ad cost or TACoS | `CLAUDE.md` · this README · [sku-allocation.md](sku-allocation.md) · `gaps.md` |
| missing hourly data | `CLAUDE.md` · this README · [ams-stream.md](ams-stream.md) · `gaps.md` |
| a report that never arrived | `CLAUDE.md` · this README · [ads-api.md](ads-api.md) · `gaps.md` |
| a campaign's figures | `CLAUDE.md` · this README · [campaigns.md](campaigns.md) · [sku-allocation.md](sku-allocation.md) · `gaps.md` |
| a term or placement decision | `CLAUDE.md` · this README · [search-terms.md](search-terms.md) · [placements.md](placements.md) · `gaps.md` |
| an uploaded day disagreeing | `CLAUDE.md` · this README · [hourly-upload.md](hourly-upload.md) · [ams-stream.md](ams-stream.md) · `gaps.md` |

## Current priorities

- `MKT-ALLOC-002` — the allocator reads a superseded, campaign-blind copy of the advertised-product data · P2
- `MKT-ALLOC-001` — the campaign → product-group map is hardcoded in a view module · P2
- `MKT-CAMP-001` — nothing flags a campaign whose profit rests on fallback margins · P2
- `MKT-AMS-001` · `MKT-ADS-001` — pipeline silence is invisible; **one health check fixes both** · P2

## Method

This section follows [methodology.md](../methodology.md) — the project standard,
distilled from Inventory. In short: read the function, not its docstring;
establish a root cause and a classification before filing a gap; name the source
of every count; and remember that nothing runs on a schedule locally.

## Related sections

- [brand-analytics](../brand-analytics/README.md) — organic search performance. Not this.
- [financials](../financials/README.md) — what the ad cost does to margin
- [reporting](../reporting/README.md) — the SKU table that carries per-SKU ad cost
