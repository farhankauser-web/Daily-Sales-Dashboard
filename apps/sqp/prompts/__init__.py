"""
apps/sqp/prompts/ — Versioned prompt templates loaded from .txt files.

Why files (not Python strings):
  - Non-developers can read and tweak them without touching Python
  - Diff-friendly when iterating on prompt wording
  - Keeps Python files small and focused on logic

Format: Each .txt file is split into two sections by the literal line
    ---USER---
Everything before that delimiter is the system prompt; everything after is the
user message template. Both sections accept Python `str.format()` placeholders.

Usage:
    sys, user = load_prompt('asin_analysis', context_json='{ ... }')
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def _read(name: str) -> tuple[str, str]:
    path = PROMPTS_DIR / f'{name}.txt'
    if not path.exists():
        raise FileNotFoundError(
            f'Prompt template not found: {path}. Create the file and re-run.'
        )
    raw = path.read_text(encoding='utf-8')
    if '---USER---' in raw:
        sys_part, user_part = raw.split('---USER---', 1)
    else:
        sys_part, user_part = raw, '{context_json}'
    return sys_part.strip(), user_part.strip()


def load_prompt(name: str, **context) -> tuple[str, str]:
    """
    Return (system_prompt, user_message) with placeholders filled in.
    Raises KeyError if a placeholder in the template wasn't supplied.
    """
    sys_tmpl, user_tmpl = _read(name)
    try:
        return (
            sys_tmpl.format(**context) if context else sys_tmpl,
            user_tmpl.format(**context) if context else user_tmpl,
        )
    except KeyError as exc:
        raise KeyError(
            f'Prompt {name!r} requires placeholder {exc} — pass it as a kwarg.'
        ) from exc


def clear_prompt_cache():
    """Drop the lru_cache so edits to .txt files are picked up without restart."""
    _read.cache_clear()
