"""
apps/sqp/insights.py — Phase-3 rule engine (placeholder).

Phase-1 ships an empty module so imports in future phases don't break.
Phase 3 will add:
  - click_share_losers(week_a, week_b)        ≥20% Δ% drop
  - high_impression_low_ctr(week)             impressions ≥ N, ctr ≤ X
  - conversion_holes(week)                     ctr ≥ N, cvr ≤ X
  - fast_growers(week_a, week_b)              volume Δ% ≥ 50%
  - seasonal_signals(weeks=12)                ≥3 consecutive WoW moves same sign
"""


def click_share_losers(*args, **kwargs):
    raise NotImplementedError("Phase 3")


def high_impression_low_ctr(*args, **kwargs):
    raise NotImplementedError("Phase 3")


def conversion_holes(*args, **kwargs):
    raise NotImplementedError("Phase 3")


def fast_growers(*args, **kwargs):
    raise NotImplementedError("Phase 3")
