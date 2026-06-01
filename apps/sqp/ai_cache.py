"""
apps/sqp/ai_cache.py — Deterministic cache for AI insights.

Hash key = SHA-256 of canonical JSON of
    {insight_type, model_family, context}
where `context` is the compressed metric dict the orchestration layer built.

If the same (insight_type, context) reappears → cache hit → zero Claude calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Optional

from django.utils import timezone

from .llm_client import LLMResponse, json_complete
from .models import AIInsightCache, AIInsightHistory

logger = logging.getLogger(__name__)


def compute_hash(
    insight_type: str,
    context:      dict,
    *,
    model_family: str = 'claude-sonnet',
) -> str:
    """
    Deterministic 64-char hex digest.
    We split the model name on '-' and keep only the first two segments so that
    minor version bumps ('claude-sonnet-4-20250514' → 'claude-sonnet-4-20260101')
    still hit the cache. Major family changes ('sonnet' → 'opus') invalidate.
    """
    payload = json.dumps(
        {
            'insight_type': insight_type,
            'model':        model_family,
            'context':      context,
        },
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def get_cached(hash_key: str) -> Optional[AIInsightCache]:
    """Return cached row + increment hit_count, or None."""
    row = AIInsightCache.objects.filter(hash_key=hash_key).first()
    if not row:
        return None
    AIInsightCache.objects.filter(pk=row.pk).update(
        hit_count   = row.hit_count + 1,
        last_hit_at = timezone.now(),
    )
    return row


def record_history(
    *,
    user,
    insight_type:  str,
    marketplace:   str,
    asin:          str,
    period_label:  str,
    cache:         Optional[AIInsightCache],
    cache_hit:     bool,
    request_payload:  dict,
    response_payload: dict,
    error_message: str = '',
) -> AIInsightHistory:
    return AIInsightHistory.objects.create(
        user              = user if user and user.is_authenticated else None,
        insight_type      = insight_type,
        marketplace       = marketplace or '',
        asin              = asin or '',
        period_label      = period_label or '',
        cache             = cache,
        cache_hit         = cache_hit,
        request_payload   = request_payload,
        response_payload  = response_payload,
        error_message     = error_message,
    )


def get_or_compute(
    *,
    insight_type:  str,
    marketplace:   str,
    asin:          str,
    period_label:  str,
    context:       dict,
    system_prompt: str,
    user_message:  str,
    user           = None,
    force_refresh: bool = False,
    max_tokens:    int = 2000,
) -> tuple[dict, dict]:
    """
    Cache-aware Claude call.
    Returns (response_json, meta_dict) where meta_dict has
        {'cache_hit', 'cache_id', 'model', 'input_tokens', 'output_tokens',
         'latency_ms', 'generated_at'}.
    Always writes a row to AIInsightHistory.
    """
    hash_key = compute_hash(insight_type, context)

    if not force_refresh:
        row = get_cached(hash_key)
        if row is not None:
            record_history(
                user=user, insight_type=insight_type,
                marketplace=marketplace, asin=asin, period_label=period_label,
                cache=row, cache_hit=True,
                request_payload={'context': context, 'forced': False},
                response_payload=row.response_json,
            )
            return row.response_json, {
                'cache_hit':     True,
                'cache_id':      row.pk,
                'model':         row.model_name,
                'input_tokens':  row.prompt_tokens,
                'output_tokens': row.response_tokens,
                'latency_ms':    row.latency_ms,
                'generated_at':  row.created_at.isoformat(),
            }

    # Cache miss → call Claude
    try:
        llm: LLMResponse = json_complete(
            system_prompt, user_message,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        record_history(
            user=user, insight_type=insight_type,
            marketplace=marketplace, asin=asin, period_label=period_label,
            cache=None, cache_hit=False,
            request_payload={'context': context, 'forced': force_refresh},
            response_payload={},
            error_message=str(exc),
        )
        raise

    parsed = llm.parsed or {}

    row, _ = AIInsightCache.objects.update_or_create(
        hash_key = hash_key,
        defaults = {
            'insight_type':    insight_type,
            'marketplace':     marketplace or '',
            'asin':            asin or '',
            'period_label':    period_label or '',
            'model_name':      llm.model,
            'prompt_tokens':   llm.input_tokens,
            'response_tokens': llm.output_tokens,
            'latency_ms':      llm.latency_ms,
            'context_json':    context,
            'response_json':   parsed,
        },
    )

    record_history(
        user=user, insight_type=insight_type,
        marketplace=marketplace, asin=asin, period_label=period_label,
        cache=row, cache_hit=False,
        request_payload={'context': context, 'forced': force_refresh},
        response_payload=parsed,
    )

    return parsed, {
        'cache_hit':     False,
        'cache_id':      row.pk,
        'model':         llm.model,
        'input_tokens':  llm.input_tokens,
        'output_tokens': llm.output_tokens,
        'latency_ms':    llm.latency_ms,
        'generated_at':  row.created_at.isoformat(),
    }
