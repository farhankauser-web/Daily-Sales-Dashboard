"""
The Opportunity Score (v2 §5).

    Score ($/month CM) = Headroom($) × WinProbability × MarginFactor

Deliberately NOT a 0-100 blended index over ten weighted inputs. Two reasons:

  1. A weighted SUM lets enormous demand paper over zero readiness — the
     classic "chase the biggest keyword" failure. Multiplication means a weak
     factor drags the whole score proportionally, which is how the decision
     actually behaves: big market × can't win it = not an opportunity yet.
  2. The output is money, so opportunities of different TYPES are comparable.
     A negative-keyword saving and a new-product launch land on one ranking
     because both are expressed as monthly contribution margin.

Confidence is scored SEPARATELY and never folded into the number: it measures
how much evidence stands behind the estimate, not how big the estimate is. A
high-score/low-confidence item is an investigation, not an investment.
"""
from . import config as cfg


def _clamp(x: float) -> float:
    """Map a raw 0-1 factor into [FACTOR_FLOOR, 1.0].

    Nothing may silently zero a score — a true zero belongs to a hard gate,
    which is reported as `blocked` with a reason the user can act on.
    """
    return max(cfg.FACTOR_FLOOR, min(1.0, x))


def conversion_factor(term_cvr: float, group_cvr: float, clicks: int) -> float:
    """Do we convert this demand? Unproven (too few clicks) sits at neutral."""
    if clicks < cfg.MIN_CLICKS_FOR_CVR_PROOF or group_cvr <= 0:
        return 0.5
    return _clamp(term_cvr / group_cvr)


def foothold_factor(share: float) -> float:
    """An existing organic foothold makes taking more of a pool likelier."""
    if share <= 0:
        return cfg.FACTOR_FLOOR + 0.15
    return _clamp(0.35 + (share / cfg.WINNING_SHARE) * 0.65)


def intensity_factor(intensity: float | None) -> float | None:
    """
    Fragmented markets are winnable; entrenched top-3s are not.

    Returns None when intensity cannot be measured, and callers OMIT the factor
    rather than substituting a number. This used to return 0.6 for unknown,
    which sounds conservative and is not: Amazon does not publish the top-3 ASIN
    arrays this is derived from (`ba_reports.py` hardcodes them empty, and the
    Item Comparison report is deprecated), so the value was unknown on every
    single opportunity. The result was a permanent 40% haircut — $19,498/mo
    across 103 opportunities — applied for a signal that carries no information.

    An unmeasurable factor is not evidence of difficulty. It is silence.
    """
    if intensity is None:
        return None
    if intensity >= cfg.INTENSITY_ENTRENCHED:
        return cfg.FACTOR_FLOOR + 0.1
    if intensity <= cfg.INTENSITY_FRAGMENTED:
        return 1.0
    span = cfg.INTENSITY_ENTRENCHED - cfg.INTENSITY_FRAGMENTED
    return _clamp(1.0 - ((intensity - cfg.INTENSITY_FRAGMENTED) / span) * 0.7)


def momentum_factor(share_trend: float | None, can_trend: bool) -> float:
    """Rising demand/share earns a premium; without enough BA weeks, neutral."""
    if not can_trend or share_trend is None:
        return cfg.MOMENTUM_NEUTRAL
    if share_trend > 0.005:
        return 1.0
    if share_trend < -0.005:
        return _clamp(0.45)
    return 0.7


def readiness_factor(inv: dict, needs_stock: bool) -> float:
    """Stock cover as a probability input (the hard block is applied above)."""
    if not needs_stock or not inv.get('has_data'):
        return 1.0
    cover = inv.get('min_cover') or 0
    if cover >= cfg.INVENTORY_WARN_DAYS:
        return 1.0
    return _clamp(cover / cfg.INVENTORY_WARN_DAYS)


def win_probability(**factors) -> tuple:
    """
    Multiply the sub-factors together. Returns (probability, factor dict).

    Factors of None are DROPPED, not defaulted. A sub-factor that cannot be
    measured must not quietly become a multiplier — see `intensity_factor`.
    """
    known = {k: v for k, v in factors.items() if v is not None}
    p = 1.0
    for v in known.values():
        p *= v
    return p, known


def confidence(clicks: int = 0, orders: int = 0, ba_weeks: int = 0,
               margin_trusted: bool = True) -> str:
    """
    Evidence quantity, not effect size (v2 §5.3).

    An untrusted margin proxy caps confidence at 'low' regardless of volume —
    the money estimate is only as good as the margin behind it.
    """
    if not margin_trusted:
        return 'low'
    if clicks >= cfg.CONFIDENCE_HIGH_CLICKS and orders >= cfg.CONFIDENCE_HIGH_ORDERS \
            and ba_weeks >= cfg.BA_WEEKS_FOR_TREND:
        return 'high'
    if clicks >= cfg.CONFIDENCE_MED_CLICKS or orders >= cfg.CONFIDENCE_MED_ORDERS:
        return 'medium'
    return 'low'


def score(headroom_value: float, win_prob: float, margin_factor: float) -> float:
    """The ranking key: expected contribution margin per month."""
    return max(0.0, headroom_value * win_prob * margin_factor)


def difficulty(opp_type: str, unmet_dependencies: int = 0) -> int:
    """Base difficulty for the type, raised by each unmet dependency."""
    base = cfg.BASE_DIFFICULTY.get(opp_type, 3)
    return min(5, base + (1 if unmet_dependencies else 0))


def quadrant(score_value: float, difficulty_value: int, median_score: float) -> str:
    """
    Board placement (v2 §4.2): value against difficulty.

    'Do now' is what the Executive Screen pulls from first.
    """
    high_value = score_value >= median_score
    easy = difficulty_value <= 2
    if high_value and easy:
        return 'do_now'
    if high_value:
        return 'plan'
    if easy:
        return 'delegate'
    return 'ignore'
