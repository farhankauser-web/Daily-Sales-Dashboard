"""
generate_ai_recommendations — Phase 4 — Claude-backed action engine.

Reads the briefing data prepared by `ai_insights.gather_briefing_data()`,
asks Claude to return a strict JSON list of actionable recommendations, then
upserts the results into `AIRecommendation`.

Determinism notes:
  • recommendation_id = SHA1(scope_type + scope_id + lowercase headline).
    Same finding tomorrow → same row updates rather than duplicates.
  • status='new' on first creation; existing rows that the user has touched
    (acknowledged/done/dismissed/snoozed) are NOT downgraded to 'new' on a
    re-run — they stay in their user-set state until snooze expires.
  • If the user has 'dismissed' a rec, we DO NOT re-create it for 14 days.

Usage:
    python manage.py generate_ai_recommendations                # yesterday, all marketplaces
    python manage.py generate_ai_recommendations --marketplace usa
    python manage.py generate_ai_recommendations --date 2026-06-10
    python manage.py generate_ai_recommendations --max-recs 12 --model claude-sonnet-4-20250514
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are a senior Amazon-PPC and brand-performance analyst writing recommendations for a private-label home goods business (towels, washcloths, bedsheets).

You will receive a JSON briefing of yesterday's KPIs and supporting context. Return a JSON ARRAY of the TOP recommendations the operator should act on RIGHT NOW. Quality over quantity.

Each recommendation must be a JSON object with these fields, ALL REQUIRED:
  "severity":        one of "critical" | "warning" | "opportunity" | "info"
  "category":        one of "ppc_scale" | "ppc_cut" | "ppc_negate" | "ppc_bid"
                          | "sku_scale" | "sku_pause" | "margin_fix"
                          | "inventory" | "listing" | "cross_sell"
                          | "competitive" | "other"
  "scope_type":      one of "campaign" | "sku" | "search_term" | "placement"
                          | "brand" | "account" | "inventory" | "other"
  "scope_id":        the campaign_id / sku / search_term being acted on (empty string for account-level)
  "scope_name":      human-readable label (campaign name, product title, term text)
  "headline":        one short imperative sentence — what to do (≤120 chars)
  "evidence":        the data that justifies the recommendation, including specific numbers
  "suggested_action":concrete next step (one or two sentences)
  "projected_impact": estimated $ impact / week OR a qualitative band like "small / medium / large"
  "confidence":      a float 0.0–1.0 for your confidence in the recommendation

Rules:
- BE CONCRETE. Reference actual campaign names, SKUs, search terms, and numbers from the briefing.
- DO NOT recommend changes you have no evidence for. If a campaign isn't in the briefing, don't invent it.
- PRIORITISE high-spend losses, scaling opportunities with strong margins, and clear data anomalies.
- DO NOT recommend pausing/disabling things — the operator wants ANALYTICS only, no automated actions.
  Frame everything as "consider", "evaluate", "review" — they decide what to action.
- Return BETWEEN 5 AND {max_recs} items.
- Reply with ONLY the JSON array. No prose, no markdown fences. Start with [ and end with ].
"""


class Command(BaseCommand):
    help = ('Generate AIRecommendation rows for one or more marketplaces using '
            'Claude + the briefing data from ai_insights.gather_briefing_data().')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--date', default=None,
                            help='Yesterday anchor (YYYY-MM-DD). Default: today-1.')
        parser.add_argument('--max-recs', type=int, default=15,
                            help='Upper bound for # of recs to ask Claude for (default 15).')
        parser.add_argument('--model', default=None,
                            help='Override model id (otherwise uses AIProviderConfig default).')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.dashboard.ai_insights import gather_briefing_data, call_anthropic
        from apps.dashboard.models import AIRecommendation

        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True
                       ).values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        max_recs = max(5, min(opts['max_recs'], 25))
        anchor   = (date.fromisoformat(opts['date']) if opts['date']
                    else date.today() - timedelta(days=1))

        total = 0
        for mp in mps:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n🤖  [{mp.upper()}] generating recommendations for {anchor}'))
            briefing = gather_briefing_data(mp, target_date=anchor)
            briefing_json = json.dumps(briefing, default=str, indent=2)

            user_msg = (
                f'Briefing for marketplace {mp.upper()} as of {anchor}:\n\n'
                f'```json\n{briefing_json}\n```\n\n'
                f'Return up to {max_recs} recommendations as the JSON array described above.'
            )
            system = _SYSTEM_PROMPT.format(max_recs=max_recs)

            result = call_anthropic(system=system, user_message=user_msg,
                                     model=opts['model'], max_tokens=4096)
            if not result['ok']:
                self.stderr.write(self.style.ERROR(
                    f"  ✗ Claude call failed: {result['error']}"))
                continue

            recs = self._parse_json(result['text'])
            if recs is None:
                self.stderr.write(self.style.ERROR(
                    '  ✗ Claude returned text that wasn\'t parseable JSON. '
                    'Sample: ' + result['text'][:200]))
                continue

            n_created, n_updated, n_skipped = self._upsert_recommendations(
                mp, anchor, recs, ai_model=result['model'],
                AIRecommendation=AIRecommendation,
            )
            total += n_created + n_updated
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {len(recs)} recs returned · {n_created} new · '
                f'{n_updated} updated · {n_skipped} skipped (dismissed within 14d)'))

        self.stdout.write(self.style.SUCCESS(
            f'\n✅  Done — {total} recommendations persisted.\n'))

    # ── Parsing & persistence ──────────────────────────────────────────────
    @staticmethod
    def _parse_json(text: str):
        """Strip code fences if present and json.loads. Returns list or None."""
        t = text.strip()
        # Strip ``` fences
        if t.startswith('```'):
            t = t.strip('`')
            # Drop optional 'json' language token
            if t.lower().startswith('json'):
                t = t[4:].lstrip()
        # Find first [ and last ]
        i = t.find('['); j = t.rfind(']')
        if i == -1 or j == -1:
            return None
        try:
            recs = json.loads(t[i:j+1])
        except json.JSONDecodeError as e:
            logger.warning('JSON decode failed: %s', e)
            return None
        return recs if isinstance(recs, list) else None

    def _upsert_recommendations(self, mp, anchor, recs, *, ai_model,
                                  AIRecommendation) -> tuple[int, int, int]:
        now = timezone.now()
        n_created = n_updated = n_skipped = 0

        # Dismissed-recently lookup so we don't keep nagging the user
        dismissed_recent = set(AIRecommendation.objects.filter(
            marketplace=mp, status='dismissed',
            updated_at__gte=now - timedelta(days=14),
        ).values_list('recommendation_id', flat=True))

        # User-touched statuses we DO NOT downgrade to 'new'
        sticky_statuses = {'acknowledged', 'done', 'snoozed', 'dismissed'}

        for r in recs:
            if not isinstance(r, dict):
                continue
            headline = (r.get('headline') or '').strip()
            if not headline:
                continue
            scope_type = (r.get('scope_type') or 'account').strip()
            scope_id   = (r.get('scope_id')   or '').strip()
            scope_name = (r.get('scope_name') or '')[:256]

            # Stable hash: same finding next run → same row
            stable_key = f'{scope_type}|{scope_id}|{headline.lower()}'.encode()
            rec_id = hashlib.sha1(stable_key).hexdigest()

            if rec_id in dismissed_recent:
                n_skipped += 1
                continue

            severity   = (r.get('severity')   or 'info').strip()
            category   = (r.get('category')   or 'other').strip()
            evidence   = (r.get('evidence')   or '').strip()
            action     = (r.get('suggested_action') or '').strip()
            impact     = (r.get('projected_impact') or '')[:128]
            try:
                confidence = float(r.get('confidence') or 0)
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = 0.5

            # Compound rank score for sort ordering
            sev_weight = {'critical': 3.0, 'warning': 2.0,
                           'opportunity': 1.5, 'info': 1.0}.get(severity, 1.0)
            rank_score = round(sev_weight * (0.5 + confidence), 2)

            defaults = {
                'generated_at':    now,
                'reference_date':  anchor,
                'severity':        severity,
                'category':        category,
                'scope_type':      scope_type,
                'scope_id':        scope_id[:64],
                'scope_name':      scope_name,
                'headline':        headline[:256],
                'evidence':        evidence,
                'suggested_action':action,
                'projected_impact':impact,
                'rank_score':      Decimal(str(rank_score)),
                'confidence':      Decimal(str(round(confidence, 2))),
                'ai_model':        ai_model[:64] if ai_model else '',
                'raw_response':    r,
            }

            obj, created = AIRecommendation.objects.get_or_create(
                marketplace=mp, recommendation_id=rec_id,
                defaults={**defaults, 'status': 'new'},
            )
            if created:
                n_created += 1
            else:
                # Only refresh the analytics fields; preserve user-set status
                # (so dismissed/done/acknowledged/snoozed survive across re-runs)
                if obj.status not in sticky_statuses:
                    obj.status = 'new'
                for k, v in defaults.items():
                    setattr(obj, k, v)
                obj.save()
                n_updated += 1

        return n_created, n_updated, n_skipped
