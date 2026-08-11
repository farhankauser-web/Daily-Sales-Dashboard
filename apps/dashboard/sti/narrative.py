"""
The AI narrator — explains the report, never authors it.

`MKT-D-014`: every recommendation traces to deterministic data. AI explains,
prioritises and summarises; it never invents an opportunity and is never the
source of truth. So this module does three things and refuses the fourth:

  1. Reads the stored payload — nothing else. No database access, no second
     opinion, no browsing.
  2. Asks for prose about opportunities THAT ALREADY EXIST, by title.
  3. Checks the answer back against the payload and flags any opportunity title
     the model mentions that the engine did not produce.

The last step is the one that matters. A narrator that can quietly add a
recommendation is an author, and an authored recommendation cannot be traced,
scored, or measured by the outcome loop — the three things that make this
module worth more than a keyword tool.

`MKT-D-013` adds a second prohibition: no causal or trend claim across periods
that cannot support one. The prompt states the periods and forbids the claim;
the payload it receives carries the period stamps that make the rule checkable.
"""
import logging
import re

logger = logging.getLogger(__name__)

MAX_OPPORTUNITIES = 12

SYSTEM = """You are briefing the head of e-commerce at a home-textiles seller on \
Amazon. You are given a completed Search Intelligence report as JSON.

Your job is to EXPLAIN what the report already says. You are a narrator, not an \
analyst with independent access.

Hard rules:
- Reference ONLY opportunities present in the JSON, by their exact title. Never \
invent, merge, rename or extrapolate one.
- Never state a number that is not in the JSON.
- Never claim cause and effect between two figures drawn from different \
reporting periods. The JSON states each figure's period; advertising and market \
data routinely describe different weeks. "Share fell because a competitor rose" \
is forbidden unless both observations sit in the same period.
- If the market data is absent for this period, say so plainly rather than \
reasoning around it.
- Money is already contribution margin per month in the marketplace's currency. \
Do not recompute or convert it.

Write for someone deciding what to do this week:
1. Two or three sentences on where this product group stands.
2. What to do first, and why that before the others.
3. What can wait, and what is not worth doing.

Be concise and specific. No headings, no bullet lists, no preamble. Under 220 \
words. Plain prose."""


def _brief(payload: dict) -> dict:
    """
    The subset of the report the narrator may see.

    Deliberately small: the opportunities, the headline numbers, and the period
    stamps that make the no-cross-period-causality rule checkable. Sending the
    whole payload would invite the model to reason about fields nobody asked it
    to interpret.
    """
    meta = payload.get('meta', {})
    kpis = payload.get('kpis', {})
    opps = payload.get('opportunities', [])[:MAX_OPPORTUNITIES]

    return {
        'group':        meta.get('group'),
        'marketplace':  meta.get('marketplace'),
        'currency':     meta.get('currency'),
        'period':       meta.get('period_label'),
        'period_coverage': meta.get('period_coverage'),
        'market_data_available': bool(meta.get('ba_weeks')),
        'market_period': (meta.get('ba_window') or {}).get('end'),
        'valuation_period': (meta.get('as_of', {}).get('valuation') or {}).get('period'),
        'headline': {
            'open_opportunity_per_month': kpis.get('open_value'),
            'ad_spend':    kpis.get('ad_spend'),
            'acos':        kpis.get('acos'),
            'market_share': kpis.get('market_share'),
            'cm_rate':     (meta.get('valuation') or {}).get('cm_rate'),
        },
        'opportunities': [
            {'title': o['title'], 'type': o['opp_type'],
             'value_per_month': o['score'], 'confidence': o['confidence'],
             'difficulty': o['difficulty'], 'timeline': o.get('timeline'),
             'blocked': o.get('blocked_reason') or None,
             'why': o['why']}
            for o in opps
        ],
        'changed_since_last_report': payload.get('diff', {}),
    }


def _unsupported_titles(text: str, payload: dict) -> list:
    """
    Any quoted phrase the narrator presents as an opportunity that the engine
    did not produce.

    Cheap and deliberately narrow: it catches the failure that matters — a
    recommendation appearing from nowhere — without trying to police prose.
    """
    known = {o['title'].lower() for o in payload.get('opportunities', [])}
    known_subjects = {(o.get('subject') or '').lower()
                      for o in payload.get('opportunities', [])}
    bad = []
    for quoted in re.findall(r'"([^"]{4,80})"', text):
        q = quoted.lower().strip()
        if q in known_subjects or any(q in k for k in known):
            continue
        bad.append(quoted)
    return bad


def generate(run) -> dict:
    """
    Narrate one stored run.

    Returns {'ok', 'text', 'error', 'warnings', 'model'}. Never raises: a
    narrator that fails must not take the report down with it, because the
    report is the product and this is commentary on it.
    """
    from ..ai_insights import call_anthropic

    payload = run.payload or {}
    if not payload.get('opportunities'):
        return {'ok': False, 'text': '', 'warnings': [],
                'error': 'This report found no opportunities to explain.'}

    import json
    brief = _brief(payload)
    result = call_anthropic(
        SYSTEM,
        'Here is the completed report.\n\n' + json.dumps(brief, indent=1, default=str),
        max_tokens=900,
    )
    if not result.get('ok'):
        return {'ok': False, 'text': '', 'warnings': [],
                'error': result.get('error') or 'The narrator did not respond.'}

    text = (result.get('text') or '').strip()
    warnings = []
    unsupported = _unsupported_titles(text, payload)
    if unsupported:
        # Surfaced, not silently trimmed. If the narrator started inventing, the
        # reader needs to know which sentence to distrust.
        warnings.append(
            'Mentions not traceable to an opportunity in this report: '
            + ', '.join(f'"{u}"' for u in unsupported[:4])
            + '. Treat those as commentary, not recommendations.')
        logger.warning('STI narrator produced untraceable mentions: %s', unsupported)

    return {'ok': True, 'text': text, 'error': None,
            'warnings': warnings, 'model': result.get('model', '')}
