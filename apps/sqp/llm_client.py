"""
apps/sqp/llm_client.py — Thin wrapper around Anthropic's Messages API.

Uses the existing `AnthropicConfig` row (or falls back to settings) for credentials
and model selection. Designed for analytical workloads: temperature=0, JSON-only
responses, retries on transient errors, hard timeout.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings

from apps.amazon_api.models import AnthropicConfig

logger = logging.getLogger(__name__)

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_API_VERSION = '2023-06-01'

# Retry on these HTTP statuses (Anthropic's transient/throttle codes)
_TRANSIENT = {408, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    """Raised when Claude returns a non-recoverable error or invalid JSON."""


@dataclass
class LLMResponse:
    text:           str
    parsed:         Optional[dict]      # parsed JSON if json_complete()
    model:          str
    input_tokens:   int
    output_tokens:  int
    latency_ms:     int
    raw:            dict                # full Anthropic response body


def _resolve_credentials() -> tuple[str, str]:
    """Returns (api_key, model). Prefers active AnthropicConfig row over settings."""
    cfg = AnthropicConfig.get_active()
    api_key = (cfg.api_key if cfg else None) or getattr(settings, 'ANTHROPIC_API_KEY', None)
    if not api_key:
        raise LLMError(
            "No Anthropic API key configured. Add one at /api-config/ or set "
            "ANTHROPIC_API_KEY in environment."
        )
    model = (cfg.model if cfg else None) or getattr(
        settings, 'ANTHROPIC_MODEL', 'claude-sonnet-4-20250514'
    )
    return api_key, model


def _extract_json(text: str) -> dict:
    """
    Pull a JSON object out of Claude's response text.
    Handles three cases:
      1. Raw JSON: '{"a":1}'
      2. Fenced ```json blocks
      3. JSON mixed with prose — extract the first balanced {...} substring
    """
    s = text.strip()
    if s.startswith('{') and s.endswith('}'):
        return json.loads(s)

    # ```json ... ```  or  ``` ... ```
    fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', s, flags=re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    # Greedy first balanced JSON object
    start = s.find('{')
    if start != -1:
        depth = 0
        for i, ch in enumerate(s[start:], start=start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return json.loads(s[start:i + 1])

    raise LLMError(f'Could not extract JSON from response (first 300 chars): {s[:300]!r}')


def complete(
    system: str,
    user_message: str,
    *,
    model:       Optional[str] = None,
    temperature: float = 0.0,
    max_tokens:  int = 2000,
    timeout:     int = 60,
    max_retries: int = 3,
) -> LLMResponse:
    """
    One-shot text completion. Returns full body + token counts.
    `temperature=0` for analytical/cacheable runs (default).
    """
    api_key, default_model = _resolve_credentials()
    chosen_model = model or default_model

    body = {
        'model':       chosen_model,
        'max_tokens':  max_tokens,
        'temperature': temperature,
        'system':      system,
        'messages':    [{'role': 'user', 'content': user_message}],
    }
    headers = {
        'x-api-key':         api_key,
        'anthropic-version': ANTHROPIC_API_VERSION,
        'Content-Type':      'application/json',
    }

    started = time.monotonic()
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=timeout)
            if r.status_code in _TRANSIENT:
                wait = min(2 ** attempt, 10)
                logger.warning('Claude %s on attempt %d, retrying in %ds', r.status_code, attempt, wait)
                time.sleep(wait)
                continue
            if not r.ok:
                raise LLMError(f'Claude HTTP {r.status_code}: {r.text[:500]}')
            data = r.json()
            content = data.get('content') or []
            text = ''.join(block.get('text', '') for block in content if block.get('type') == 'text')
            usage = data.get('usage') or {}
            latency_ms = int((time.monotonic() - started) * 1000)
            return LLMResponse(
                text=text,
                parsed=None,
                model=data.get('model', chosen_model),
                input_tokens=int(usage.get('input_tokens')  or 0),
                output_tokens=int(usage.get('output_tokens') or 0),
                latency_ms=latency_ms,
                raw=data,
            )
        except requests.RequestException as exc:
            last_exc = exc
            wait = min(2 ** attempt, 10)
            logger.warning('Claude network error on attempt %d (%s), retrying in %ds', attempt, exc, wait)
            time.sleep(wait)

    raise LLMError(f'Claude request failed after {max_retries} attempts: {last_exc}')


def json_complete(
    system: str,
    user_message: str,
    *,
    model:       Optional[str] = None,
    temperature: float = 0.0,
    max_tokens:  int = 2000,
    timeout:     int = 60,
    max_retries: int = 3,
) -> LLMResponse:
    """
    Same as `complete()` but parses the response as JSON.
    Adds a strict 'JSON only' instruction to the system prompt as a safety net
    in case the caller's prompt forgot to.
    """
    strict_system = system.rstrip() + (
        "\n\nRespond with VALID JSON ONLY — no preamble, no explanations, no markdown fences. "
        "If you cannot answer with the requested fields, return JSON with empty strings/arrays "
        "rather than omitting keys."
    )
    resp = complete(
        strict_system, user_message,
        model=model, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, max_retries=max_retries,
    )
    try:
        resp.parsed = _extract_json(resp.text)
    except json.JSONDecodeError as exc:
        raise LLMError(f'Invalid JSON from Claude: {exc}; text={resp.text[:300]!r}')
    return resp
