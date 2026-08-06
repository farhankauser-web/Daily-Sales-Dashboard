# AI briefings

files: `apps/dashboard/ai_insights.py`
       `apps/dashboard/views.py` — `summary_stream`, `morning_report`, `ai_recommendations`
verified against: `82744aa` · 2026-08-06

Written narrative over the figures: a morning report, an executive summary and
per-page recommendations.

## Purpose

A table shows what happened; it does not say what is worth doing. These
briefings put the day's figures into sentences, rank what changed, and suggest
what to look at — so the first five minutes of the day are spent deciding rather
than reading.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| A briefing bundle | per marketplace, per day | every figure the narrative mentions |
| Anthropic API | per request | the wording, and nothing else |

**The model never computes.** The bundle is assembled first — yesterday's P&L,
best and worst campaigns, best and worst SKUs, open alerts, share and basket
signals — and passed in complete. See `INT-D-001`.

## Business rules

1. **Every number in a briefing was computed before the prompt was built.** The
   model is given figures and asked to explain them; it is never asked to derive
   one.
2. **The briefing bundle is a stable, serialisable shape**, shared by every
   prompt, so all briefings describe the same underlying facts.
3. **Per-page commentary uses the same shape, scoped narrower** — a campaign or a
   SKU — so a page's commentary cannot contradict the morning report.
4. **Credentials resolve in a fixed order**, from stored provider configuration
   through to settings, so a deployment can override without code changes.
5. **A briefing is generated for a marketplace and a date**, never "now" — the
   same request produces the same inputs.

## Edge cases

- **A day with missing figures.** The bundle carries what exists; the narrative
  describes less rather than inventing more.
- **The AI provider unavailable.** The figures remain; the narrative does not.
  The underlying pages are unaffected because nothing depends on the prose.
- **A recommendation acted on.** Moves through acknowledged, done, snoozed or
  dismissed — the states exist because "I have seen this" and "I have fixed
  this" are different.

## Observations — not gaps

*Source: local development data; provisional.* No recommendations exist locally;
generation is scheduled and runs in production.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INT-001` | Command Center phase 3 — per-widget configuration and resizing | missing implementation |

## Related decisions

`INT-D-001`

## Related documents

- [alerts.md](alerts.md) — the conditions a briefing reports
- [reporting/command-center.md](../reporting/command-center.md) — the board these feed
