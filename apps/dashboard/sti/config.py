"""
Every tunable number for the Search Intelligence Center lives here.

Rule: no business threshold is written anywhere else in the sti package. If a
generator needs a cut-off, it imports it from this module. These are business
judgements, not derived values — they are meant to be reviewed and changed by
a human, and a single file makes that review possible.

Values marked (v2 §n) trace back to the approved design document.
"""

# ── Report windows ───────────────────────────────────────────────────────────
# T-2 is the business rule: advertising and order data are considered settled
# by then (7-day attribution means the last few days still accrue, which the
# report states rather than hides).
ANCHOR_OFFSET_DAYS = 2

DATE_PRESETS = {
    # id      label            days (None = single anchor day)
    'day':   ('T-2 (single day)', 1),
    '7d':    ('Last 7 days',      7),
    '14d':   ('Last 14 days',     14),
    '30d':   ('Last 30 days',     30),
}
DEFAULT_PRESET = '30d'

# Bounds the cost of the term-spine query (v2 §2).
MAX_RANGE_DAYS = 90

# Days per month used to convert an observed window into a monthly rate. All
# opportunity money is expressed per month so values are comparable.
DAYS_PER_MONTH = 30.44
WEEKS_PER_MONTH = 4.35


# ── Brand Analytics window (the second clock, v2 §1.4) ───────────────────────
# SQP is weekly and lands ~T-3 after the week closes. The BA window is DERIVED
# from the ads range, never date-matched to it.
BA_WEEKS_MIN = 1          # below this, BA-fed sections are suppressed entirely
BA_WEEKS_FOR_TREND = 3    # below this, no momentum/trend claims are made
BA_WEEKS_MAX = 13         # never pull more than a quarter

# How far behind the ads window the newest SQP week may sit before the market
# read is labelled STALE. Found in testing: the dev snapshot holds a single
# 2026-05-31 week, which the window rule happily paired with an August ads
# range — a nine-week-old market picture presented as current. The data is
# still the best available and is kept, but it is labelled loudly rather than
# passed off as concurrent.
BA_MAX_STALENESS_DAYS = 21

# When fewer than BA_WEEKS_FOR_TREND weeks exist, momentum is unknown. A
# neutral (not zero, not one) multiplier keeps an unknown from dominating.
MOMENTUM_NEUTRAL = 0.60


# ── Materiality floors ───────────────────────────────────────────────────────
# Below these, a term is noise and must not generate an opportunity or an
# ops-queue item. Money floors are in the marketplace's own currency.
MIN_CLICKS_FOR_CVR_PROOF = 15     # fewer clicks than this proves nothing
MIN_ORDERS_FOR_SCALE     = 3      # scaling needs demonstrated conversion
MIN_SPEND_FOR_WASTE      = 15.0   # a zero-order term below this is not worth a card
MIN_CLICKS_FOR_WASTE     = 8      # stops one-click noise inflating wasted spend
MIN_IMPRESSIONS_FOR_CTR  = 500

# Market pools smaller than this (weekly purchases) are not worth a business
# case at our scale.
MIN_MARKET_PURCHASES_WK  = 25


# ── Performance bands (v2 §5, ACOS bands only used for evidence colouring) ───
ACOS_GOOD = 0.25
ACOS_WARN = 0.45
ROAS_GOOD = 4.0
ROAS_WARN = 2.0

# A term is a scale candidate when its ACOS is comfortably under the group's.
SCALE_ACOS_RATIO   = 0.60   # term ACOS <= 60% of group ACOS
# A term is a listing problem when it attracts clicks but does not convert.
LISTING_CTR_RATIO  = 1.50   # term CTR >= 150% of group CTR
LISTING_CVR_RATIO  = 0.50   # term CVR <= 50%  of group CVR
# Off-category spend above this is a negative-keyword candidate.
NEGATIVE_MIN_SPEND = 10.0

# ── Conquest (competitor ASIN targeting) ─────────────────────────────────────
# The competitor detectors originally designed (riser / fader / vulnerable /
# fortress) cannot be built: Amazon does not publish per-query top-3 ASINs
# (ba_reports.py writes those arrays empty by design) and the Item Comparison
# report is deprecated as of 2026. What IS measurable is our own conquest
# spend — the ASIN targets we bid on — so that is what the conquest opportunity
# reasons about.
CONQUEST_MIN_SPEND   = 50.0   # below this the portfolio is not worth a card
CONQUEST_POOR_RATIO  = 1.30   # conquest ACOS >= this x group ACOS -> cut
CONQUEST_GOOD_RATIO  = 0.75   # conquest ACOS <= this x group ACOS -> expand


# ── Market share model (v2 §7.2) ─────────────────────────────────────────────
# Headroom is NOT (100% - share). The ceiling is what we already achieve on
# comparable nodes; when there is nothing comparable, fall back to this.
FALLBACK_ATTAINABLE_SHARE = 0.03      # 3%
# Never assume we can more than triple our current share in one planning cycle.
MAX_SHARE_MULTIPLE        = 3.0
# A share at or above this on a pool means we are winning it already.
WINNING_SHARE             = 0.10

# Organic gap: big pool, we convert, but our purchase share is thin.
ORGANIC_GAP_MAX_SHARE     = 0.05
# Paid dependency: ad orders as a fraction of all our purchases on a query.
PAID_DEPENDENCY_RATIO     = 0.80


# ── Win probability sub-factors (v2 §5.1) ────────────────────────────────────
# Each sub-factor maps into [FACTOR_FLOOR, 1.0] so no single input can silently
# zero a score — a zero belongs to a hard gate, which is reported, not hidden.
FACTOR_FLOOR = 0.20

# Competitive intensity: share of clicks held by the top-3 ASINs on a query.
# Concentrated markets are harder to enter.
INTENSITY_ENTRENCHED = 0.80   # top-3 hold >=80% of clicks
INTENSITY_FRAGMENTED = 0.40


# ── Valuation basis (MKT-D-012) ──────────────────────────────────────────────
# Contribution margin and average selling price price an opportunity. They are
# STRUCTURAL properties, not period-sensitive levels — measured across three
# months the USA rate moved 30.3% → 30.1% → 28.7% and ASP sat at ~$36 — so they
# read from a long trailing window of settled per-SKU data, independent of the
# report window. Tying them to the report window is what produced a silent
# fallback on 7-day reports, where no settled revenue existed at all.
VALUATION_TRAILING_DAYS = 90
# Below this many selling days in the trailing window the basis is not trusted.
VALUATION_MIN_DAYS = 5

# Paid share divides ad sales by settled revenue. Those sources cover different
# spans, so the ratio uses only the days both cover — and below this many days
# the denominator is too thin to mean anything. Measured: a 14-day report
# overlapped just ONE selling day, producing a 2074% "paid share". Withheld with
# a reason beats a number that is arithmetically valid and practically absurd.
PAID_SHARE_MIN_DAYS = 7


# ── Margin ───────────────────────────────────────────────────────────────────
# Used when CampaignProfitDaily has no usable coverage for the group. Referral
# fee only — deliberately pessimistic, and it caps confidence at 'low'.
FALLBACK_CM_RATE          = 0.20
# Below this attribution coverage the campaign profit proxy is not trusted.
MIN_ATTRIBUTION_COVERAGE  = 40.0
# Sanity band for a derived contribution-margin rate. A value outside this is
# an attribution artefact, not a real margin, and falls back rather than
# flattering every opportunity that multiplies by it.
CM_RATE_MIN = 0.05
CM_RATE_MAX = 0.70


# ── Readiness gates (v2 §4.1) ────────────────────────────────────────────────
# Recommending more spend into a stockout is worse than recommending nothing.
INVENTORY_BLOCK_DAYS = 14.0    # below this cover, spend opportunities are blocked
INVENTORY_WARN_DAYS  = 30.0


# ── Confidence (v2 §5.3) — evidence quantity, not effect size ────────────────
CONFIDENCE_HIGH_CLICKS = 150
CONFIDENCE_MED_CLICKS  = 40
CONFIDENCE_HIGH_ORDERS = 15
CONFIDENCE_MED_ORDERS  = 4


# ── Difficulty by opportunity type (v2 §4.1) ─────────────────────────────────
BASE_DIFFICULTY = {
    'defend':        1,   # add a negative / drop a bid — assets exist
    'scale_ppc':     2,   # new target or campaign needed
    'listing_fix':   3,   # listing copy changes
    'organic_push':  4,   # rank building: sustained effort over weeks
    'capture_share': 4,
    'conquest':      3,
    'product_gap':   5,   # a product does not exist yet
}

TIMELINE = {
    'defend':        'Days',
    'scale_ppc':     'Days',
    'listing_fix':   '1-2 weeks',
    'organic_push':  '4-8 weeks',
    'capture_share': '4-8 weeks',
    'conquest':      '2-4 weeks',
    'product_gap':   '3-6 months',
}


# ── Lifecycle ────────────────────────────────────────────────────────────────
# Runs where a generator stops emitting an open opportunity before it expires.
EXPIRE_AFTER_UNSEEN_RUNS = 6

# Opportunity board caps — keeps payloads bounded and the page readable.
MAX_OPPORTUNITIES_PER_TYPE = 25
MAX_OPS_QUEUE_ROWS         = 50
