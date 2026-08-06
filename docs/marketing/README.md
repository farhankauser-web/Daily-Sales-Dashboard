# Marketing

Amazon advertising: what we spend, what it returns, and which SKU it returned it
for. Five nav items, two independent data pipelines, and the only place in Pulse
where a cost has to be attributed rather than simply recorded.

## Purpose

This section owns **PPC** — Sponsored Products, Brands and Display. Campaign
performance, search terms, placements, the spend-to-SKU attribution that makes
per-SKU profit possible, and the two pipelines that feed all of it.

It does not own organic search performance. Search-query share, market share and
baskets belong to **brand-analytics** *(pending)*. The distinction is worth
holding onto: this section is *paid*, that one is *earned*.

It does not own the profit those campaigns contribute to. Margin, COGS and the
P&L belong to **financials** *(pending)*. This section supplies the ad cost;
that one decides what it means.

## Features

| Document | Covers | Open here when |
|---|---|---|
| [ads-api.md](ads-api.md) | the settled daily reports — submit, poll, download | a report never arrives, or arrives empty |
| [ams-stream.md](ams-stream.md) | S3 + Firehose → hourly campaign figures | hourly data is missing or a subscription fails |
| [hourly-upload.md](hourly-upload.md) | Seller Central hourly CSV → the same table | an uploaded day disagrees with the stream |
| [sku-allocation.md](sku-allocation.md) | campaign spend → SKU | per-SKU ad cost or TACoS looks wrong |
| campaigns.md *(pending)* | campaign centre, profit, detail tabs | a campaign's figures look wrong |
| search-terms.md *(pending)* | search-term performance and summaries | a term's spend or conversion looks wrong |
| placements.md *(pending)* | placement performance | placement multipliers or splits look wrong |

## Relationships

```
Ads API  ──daily reports──→ campaign · targeting · search term · placement snapshots
                                              ↓
AMS stream ──hourly──→ hourly snapshots       ↓
                                              ↓
                              SKU allocation: spend → SKU
                                              ↓
                          per-SKU ad cost → TACoS, CM%, campaign profit
```

Two facts about this shape cause most confusion:

- **Two pipelines, different grains and different latencies.** The Ads API
  delivers complete daily reports a day or two late. The AMS stream delivers
  hourly records continuously. They are not alternatives — the daily report is
  the settled figure and the stream is the early signal.
- **Attribution is a decision, not a fact.** Amazon reports spend per campaign,
  not per SKU. Everything per-SKU in Pulse — ad cost, TACoS, contribution
  margin — rests on the allocation this section performs.

## Ground truth

Established 2026-08-06, before any document was written. *Source: dev snapshot;
provisional against production.*

| Pipeline | State |
|---|---|
| AMS stream | **live** — 9,052 objects processed, most recent today |
| Ads API daily snapshots | **live** — search terms, targeting, placements and campaign profit all current to 2026-08-04 |
| Campaign snapshots | current to 2026-07-26 |
| SKU allocation | **last run 2026-06-16** — 555,305 rows, then nothing |
| Stream subscriptions | 12 rows across 6 datasets, a mix of `ACTIVE` and `FAILED_PROVISIONING` |
| Marketplaces | `usa` and `uk` only |

Unlike Inventory, **these paths have genuinely run**, so data findings here
carry real weight.

**Both freshness divergences are resolved and neither was a defect.** No
crontab is installed on the development machine — `deploy/crontab.txt` specifies
33 jobs and `crontab -l` reports none — so every date difference above reflects
which command someone last ran by hand. The allocator runs correctly on demand.
Staleness is not evidence of a defect in this section.

## Navigation

| Working on… | Load |
|---|---|
| per-SKU ad cost or TACoS | `CLAUDE.md` · this README · [sku-allocation.md](sku-allocation.md) · `gaps.md` |
| missing hourly data | `CLAUDE.md` · this README · [ams-stream.md](ams-stream.md) · `gaps.md` |
| a report that never arrived | `CLAUDE.md` · this README · [ads-api.md](ads-api.md) · `gaps.md` |
| a campaign's figures | `CLAUDE.md` · this README · campaigns.md *(pending)* · [sku-allocation.md](sku-allocation.md) · `gaps.md` |

## Method

This section follows [methodology.md](../methodology.md) — the project standard,
distilled from Inventory. In short: read the function, not its docstring;
establish a root cause and a classification before filing a gap; name the source
of every count; and remember that nothing runs on a schedule locally.

## Related sections

- `docs/brand-analytics/` *(pending)* — organic search performance. Not this.
- `docs/financials/` *(pending)* — what the ad cost does to margin
- `docs/reporting/` *(pending)* — the SKU table that carries per-SKU ad cost
