"""
Search Intelligence Center — the decision engine behind
/dashboard/search-intelligence/.

Design: plans/search-intelligence-center.md (v2).

Module map (deliberately small, single-purpose modules — no service classes):

    config.py        every tunable business threshold, in ONE place
    lexicon.py       term vocabulary as data (per lexicon key, per language)
    taxonomy.py      classify_term() → multi-dimensional tags
    scope.py         ProductGroup → campaign ids / ASINs / SKUs
    spine.py         the ads term aggregate (the money spine)
    market.py        Brand Analytics join → market size + our share
    readiness.py     inventory / listing / campaign-asset checks
    scoring.py       Headroom($) × WinProbability × MarginFactor
    opportunities.py one generator function per opportunity type
    runner.py        orchestration → StiReportRun.payload + persisted opportunities
"""
