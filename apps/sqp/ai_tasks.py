"""
apps/sqp/ai_tasks.py — Background-job interface for AI insights.

Phase A ships SYNCHRONOUS execution: every view-triggered analysis blocks the
request for the 3–8 s Claude call. That's acceptable for current traffic.

Why a stub file then? So Phase B/C can swap to Celery / Django-Q without
touching the call-sites. Today every caller does:

    from apps.sqp.ai_tasks import enqueue_asin_analysis
    result = enqueue_asin_analysis(...)   # returns InsightResponse directly

When we add a worker later, the same function will return a Job/Future and
the caller polls. Keeping the entry point stable means no view changes.

To migrate later:
  1. Add Celery (or Django-Q) to settings.
  2. Decorate the body of `enqueue_asin_analysis` with @shared_task.
  3. Change the return type to AsyncResult / Job — callers already check the
     `meta['cache_hit']` flag so they can branch on "ready vs pending".
"""
from __future__ import annotations

from .ai_insights import analyze_asin, InsightResponse


def enqueue_asin_analysis(
    marketplace:   str,
    asin:          str | None,
    comparison:    str = 'wow',
    *,
    user           = None,
    force_refresh: bool = False,
) -> InsightResponse:
    """
    Phase A: synchronous wrapper around analyze_asin().
    Phase B: will be a Celery @shared_task decorator and return AsyncResult.
    """
    return analyze_asin(
        marketplace,
        asin,
        comparison=comparison,
        user=user,
        force_refresh=force_refresh,
    )
