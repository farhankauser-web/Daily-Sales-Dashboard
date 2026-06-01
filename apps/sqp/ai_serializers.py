"""
apps/sqp/ai_serializers.py — Shape AI insight responses for the wire.
Keeps the API stable as ai_insights.py evolves.
"""
from __future__ import annotations

from .ai_insights import InsightResponse


def insight_to_api(insight: InsightResponse, *, include_context: bool = False) -> dict:
    """
    Standard JSON envelope for /sqp/api/ai/... endpoints.

    `include_context=True` ships the aggregated metric dict back to the client —
    useful for the AI panel that wants to render both the LLM output and the
    raw numbers it was based on (helps users see what Claude saw).
    """
    body = {
        'summary':              insight.summary,
        'key_findings':         insight.key_findings,
        'risks':                insight.risks,
        'opportunities':        insight.opportunities,
        'recommended_actions':  insight.recommended_actions,
        'priority_score':       insight.priority_score,
        'meta':                 insight.meta,
    }
    if include_context:
        body['context'] = insight.context
    return body
