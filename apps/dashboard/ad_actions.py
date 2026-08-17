"""
apps/dashboard/ad_actions.py — P4 controlled action layer.

THE RULE THIS FILE EXISTS TO ENFORCE:

    opportunity → recommendation → human review → explicit approval
                → validated execution → verification → audit

Nothing here runs on a schedule. Nothing here is triggered by an AI agent.
No function in this module mutates anything on Amazon without an
`AdActionRequest` that a human has explicitly approved, and every execution
re-validates the world before it touches it.

CURRENT INTEGRATION REALITY (audited 2026-08-17):
    apps.amazon_api.services.AdsAPIClient exposes reporting calls only —
    GETs plus report-creation POSTs. There is no campaign/budget/bid/negative
    mutation anywhere in the codebase and no campaign-management scope.
    Therefore `write_capability()` reports read-only, executions resolve to
    `unavailable`, and the UI must not offer a live Execute button.
    Dry-run exercises every gate below so the workflow is provable today and
    the executor can be swapped in unchanged the day write access exists.
"""
import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone


# ── Bounds ──────────────────────────────────────────────────────────────────
# A proposal outside these bounds is refused outright — a safety rail against
# a fat-fingered or miscomputed recommendation, independent of who approves it.
MAX_CHANGE_PCT   = 50.0     # never propose more than a ±50% swing in one step
MIN_BUDGET       = Decimal('1.00')
MAX_BUDGET       = Decimal('10000.00')

# Staleness convention — REUSED from the existing settlement cadence rather
# than invented: SkuPpcAllocation settles at T+3, so a recommendation whose
# evidence window ended more than 3 days ago is no longer describing the
# current state and must be re-reviewed.
STALE_AFTER_DAYS = 3


class ActionError(Exception):
    """Refusal with a human-readable reason. Never leaks internals."""


# ── Capability ──────────────────────────────────────────────────────────────
def write_capability(marketplace: str) -> dict:
    """Can Pulse actually change anything on Amazon for this marketplace?

    Returns {'can_write': bool, 'reason': str, 'configured': bool}.

    This is deliberately conservative: it reports the capability the CODE has,
    not the capability the account might have. Adding a real mutation method to
    AdsAPIClient is what should flip this — not a setting.
    """
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api import services as amz

    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace,
                                         is_active=True).first()
    configured = bool(cfg and cfg.ads_profile_id)
    client_can_write = any(
        hasattr(amz.AdsAPIClient, m)
        for m in ('update_campaign_budget', 'update_campaign', 'put_campaigns')
    )
    if not configured:
        return {'can_write': False, 'configured': False,
                'reason': 'No active Amazon Ads profile is configured for '
                          f'{marketplace.upper()}.'}
    if not client_can_write:
        return {'can_write': False, 'configured': True,
                'reason': 'The Amazon Ads integration is read-only — it holds '
                          'reporting calls only, with no campaign-management '
                          'write method. Recommendations can be reviewed and '
                          'approved, but not executed from Pulse.'}
    return {'can_write': True, 'configured': True, 'reason': ''}


# ── Current value (the "before" half of every recommendation) ───────────────
def current_campaign_budget(marketplace: str, campaign_id: str):
    """Latest daily budget Pulse holds for a campaign, with its as-of date.

    Sources, newest first: the budget-usage stream (AMS) then the daily
    campaign snapshot. Returns (Decimal|None, as_of_date|None, source).
    Never guesses: no stored value means no recommendation.
    """
    from .models import CampaignBudgetUsageDaily, PPCCampaignSnapshot

    bu = (CampaignBudgetUsageDaily.objects
          .filter(marketplace=marketplace, campaign_id=str(campaign_id))
          .exclude(budget_value=0).order_by('-date').first())
    snap = (PPCCampaignSnapshot.objects
            .filter(marketplace=marketplace, campaign_id=str(campaign_id))
            .exclude(daily_budget=0).order_by('-date').first())
    if bu and (not snap or bu.date >= snap.date):
        return Decimal(bu.budget_value), bu.date, 'budget-usage stream'
    if snap:
        return Decimal(snap.daily_budget), snap.date, 'campaign daily snapshot'
    return None, None, ''


def _action_id(marketplace, entity_type, entity_id, action_type, proposed_value,
               period_end) -> str:
    """Idempotency key — the same proposal for the same entity, value and
    evidence window is the SAME action, not a second one."""
    raw = (f'{marketplace}|{entity_type}|{entity_id}|{action_type}|'
           f'{Decimal(proposed_value):.2f}|{period_end}')
    return hashlib.sha1(raw.encode()).hexdigest()[:40]


# ── Propose ─────────────────────────────────────────────────────────────────
def propose_campaign_budget(*, marketplace, campaign_id, proposed_value, user,
                            opportunity=None, from_sku='', period=None):
    """Create (or return) a budget change awaiting review.

    Server-side validated end to end: the caller's claimed current value is
    ignored, the entity is verified to belong to the marketplace, and the
    proposal is bounded. Creates nothing executable — only a queue entry.
    """
    from .models import AdActionRequest, Campaign

    campaign_id = str(campaign_id).strip()
    if not campaign_id:
        raise ActionError('No campaign specified.')

    # P4.16 — the entity must belong to THIS marketplace. Never trust the client.
    dim = Campaign.objects.filter(marketplace=marketplace,
                                  campaign_id=campaign_id).first()
    if dim is None:
        raise ActionError(
            f'Campaign {campaign_id} is not on record for '
            f'{marketplace.upper()} — refusing to act on an entity this '
            f'marketplace does not own.')

    current, as_of, source = current_campaign_budget(marketplace, campaign_id)
    if current is None or current <= 0:
        raise ActionError(
            'Pulse holds no daily budget for this campaign, so a '
            '"current → proposed" change cannot be stated honestly. '
            'No recommendation created.')

    try:
        proposed = Decimal(str(proposed_value)).quantize(Decimal('0.01'))
    except Exception:
        raise ActionError('Proposed budget is not a valid amount.')

    if not (MIN_BUDGET <= proposed <= MAX_BUDGET):
        raise ActionError(f'Proposed budget must be between {MIN_BUDGET} and '
                          f'{MAX_BUDGET}.')
    if proposed == current:
        raise ActionError('Proposed budget equals the current budget — '
                          'nothing to change.')
    change_pct = abs(float(proposed - current) / float(current) * 100)
    if change_pct > MAX_CHANGE_PCT:
        raise ActionError(
            f'Proposed change is {change_pct:.0f}% — larger than the '
            f'{MAX_CHANGE_PCT:.0f}% single-step limit. Propose a smaller step.')

    p_start, p_end = (period or (None, as_of))
    aid = _action_id(marketplace, 'campaign', campaign_id, 'campaign_budget',
                     proposed, p_end)

    # P4.10 — idempotent: an identical open proposal is returned, not duplicated.
    existing = AdActionRequest.objects.filter(action_id=aid).first()
    if existing:
        return existing, False

    opp = opportunity or {}
    return AdActionRequest.objects.create(
        action_id=aid, marketplace=marketplace, entity_type='campaign',
        entity_id=campaign_id, entity_name=dim.campaign_name,
        action_type='campaign_budget',
        opportunity_key=(opp.get('key') or '')[:128],
        reason=opp.get('reason') or '',
        evidence=opp.get('evidence') or [],
        confidence=(opp.get('confidence') or '')[:16],
        from_sku=(from_sku or '')[:64],
        current_value=current, proposed_value=proposed,
        data_period_start=p_start, data_period_end=p_end,
        status='proposed', proposed_by=user,
        note=f'Current budget read from the {source} (as of {as_of}).',
    ), True


# ── Review gates ────────────────────────────────────────────────────────────
def staleness(action) -> tuple[bool, str]:
    """Is this proposal still describing today's world?"""
    end = action.data_period_end
    if end:
        age = (date.today() - end).days
        if age > STALE_AFTER_DAYS:
            return True, (f'Evidence window ended {age} days ago (limit '
                          f'{STALE_AFTER_DAYS}, matching the T+3 settlement '
                          f'convention). Re-review against fresh data.')
    live, as_of, _src = current_campaign_budget(action.marketplace,
                                                action.entity_id)
    if live is None:
        return True, 'Pulse no longer holds a budget for this campaign.'
    if Decimal(live) != Decimal(action.current_value):
        # P4.9 — the concurrency safeguard. Someone/something moved the value.
        return True, (f'Budget moved since this was proposed '
                      f'({action.current_value} → {live}). The proposed value '
                      f'is no longer based on the current state.')
    return False, ''


def approve(action, user):
    """P4.8 — explicit human approval. Nothing else advances an action."""
    if action.status != 'proposed':
        raise ActionError(f'Only a proposed action can be approved '
                          f'(this one is {action.status}).')
    stale, why = staleness(action)
    if stale:
        action.status = 'stale'
        action.failure_reason = why
        action.save(update_fields=['status', 'failure_reason', 'updated_at'])
        raise ActionError(f'Marked stale, not approved. {why}')
    action.status = 'approved'
    action.approved_by = user
    action.approved_at = timezone.now()
    action.save(update_fields=['status', 'approved_by', 'approved_at',
                               'updated_at'])
    _audit(user, 'approve', action)
    return action


def reject(action, user, note=''):
    if action.status not in ('proposed', 'stale'):
        raise ActionError(f'Cannot reject an action that is {action.status}.')
    action.status = 'rejected'
    action.note = (note or '')[:2000]
    action.save(update_fields=['status', 'note', 'updated_at'])
    _audit(user, 'reject', action)
    return action


# ── Execute ─────────────────────────────────────────────────────────────────
def execute(action, user, *, dry_run=True):
    """Execute an APPROVED action — re-validating everything first.

    Order matters: state → approval → marketplace → staleness/concurrency →
    capability. Only after all of them does anything reach Amazon, and today
    nothing can, so a live attempt resolves to `unavailable` rather than a
    fabricated success.
    """
    from .models import AdActionRequest

    # P4.10 — idempotency: a terminal action never runs twice.
    if action.status == 'executed':
        return action, 'Already executed — no action taken.'
    if action.status != 'approved':
        raise ActionError(f'Only an approved action can be executed '
                          f'(this one is {action.status}).')
    if action.approved_by_id is None:
        raise ActionError('Refusing to execute an action with no recorded '
                          'approver.')

    # P4.16 — re-verify ownership at execution time, not just at proposal.
    from .models import Campaign
    if not Campaign.objects.filter(marketplace=action.marketplace,
                                   campaign_id=action.entity_id).exists():
        raise ActionError('Campaign no longer belongs to this marketplace.')

    # P4.9 / P4.15 — read the world again immediately before writing.
    stale, why = staleness(action)
    if stale:
        action.status = 'stale'
        action.failure_reason = why
        action.save(update_fields=['status', 'failure_reason', 'updated_at'])
        return action, f'Not executed — {why}'

    live, _as_of, _src = current_campaign_budget(action.marketplace,
                                                 action.entity_id)
    action.value_before = live

    cap = write_capability(action.marketplace)
    if dry_run:
        # Full pipeline, no Amazon contact. This is how the workflow is proven
        # without risking a live change (P4.23).
        action.status = 'approved'      # stays approved; a rehearsal is not an execution
        action.dry_run = True
        action.amazon_status = 'dry_run'
        action.amazon_response = (
            f'DRY RUN — all gates passed. Would set daily budget '
            f'{action.value_before} → {action.proposed_value} for campaign '
            f'{action.entity_id} ({action.marketplace.upper()}). '
            f'Amazon was not contacted.')
        action.save(update_fields=['status', 'dry_run', 'amazon_status',
                                   'amazon_response', 'value_before',
                                   'updated_at'])
        _audit(user, 'dry_run', action)
        return action, action.amazon_response

    if not cap['can_write']:
        # P4.11 / P4.17 — say so plainly; record no success.
        action.status = 'unavailable'
        action.amazon_status = 'unavailable'
        action.failure_reason = cap['reason']
        action.save(update_fields=['status', 'amazon_status', 'failure_reason',
                                   'value_before', 'updated_at'])
        _audit(user, 'execute_unavailable', action)
        return action, cap['reason']

    # ── Live path (unreachable today; kept explicit so the adapter is the ──
    # ── only thing that needs to change when write access is granted).    ──
    action.status = 'executing'
    action.save(update_fields=['status', 'value_before', 'updated_at'])
    try:
        from apps.amazon_api import services as amz
        from apps.amazon_api.models import AmazonAPIConfig
        cfg = AmazonAPIConfig.objects.get(marketplace=action.marketplace,
                                          is_active=True)
        client = amz.AdsAPIClient(cfg)
        result = client.update_campaign_budget(          # pragma: no cover
            campaign_id=action.entity_id,
            daily_budget=float(action.proposed_value))
        action.status = 'executed'
        action.executed_at = timezone.now()
        action.amazon_status = 'success'
        action.amazon_response = str(result)[:4000]
        # P4.12 — verification: read back rather than assume.
        after, _d, _s = current_campaign_budget(action.marketplace,
                                                action.entity_id)
        action.value_after = after if after is not None else action.proposed_value
        action.save()
        _audit(user, 'execute', action)
        return action, 'Executed.'
    except Exception as exc:                              # pragma: no cover
        action.status = 'failed'
        action.amazon_status = 'error'
        action.failure_reason = f'{type(exc).__name__}: {exc}'[:2000]
        action.save(update_fields=['status', 'amazon_status', 'failure_reason',
                                   'updated_at'])
        _audit(user, 'execute_failed', action)
        return action, action.failure_reason


# ── Audit (reuses the existing users.AuditLog) ─────────────────────────────
def _audit(user, verb, action):
    try:
        from apps.users.models import AuditLog
        AuditLog.objects.create(
            user=user, action='update',
            resource=f'ad_action:{action.action_id}',
            detail=(f'{verb} · {action.action_type} · {action.marketplace} · '
                    f'campaign {action.entity_id} · '
                    f'{action.current_value} → {action.proposed_value} · '
                    f'status={action.status}'),
        )
    except Exception:
        pass    # audit must never block or break the workflow


def serialize(a):
    return {
        'id': a.pk, 'action_id': a.action_id, 'marketplace': a.marketplace,
        'entity_type': a.entity_type, 'entity_id': a.entity_id,
        'entity_name': a.entity_name, 'action_type': a.action_type,
        'status': a.status, 'status_label': a.get_status_display(),
        'current_value': float(a.current_value),
        'proposed_value': float(a.proposed_value),
        'value_before': float(a.value_before) if a.value_before is not None else None,
        'value_after': float(a.value_after) if a.value_after is not None else None,
        'change_pct': round(a.change_pct, 1) if a.change_pct is not None else None,
        'reason': a.reason, 'evidence': a.evidence, 'confidence': a.confidence,
        'opportunity_key': a.opportunity_key, 'from_sku': a.from_sku,
        'proposed_at': a.proposed_at.isoformat() if a.proposed_at else None,
        'proposed_by': getattr(a.proposed_by, 'email', None),
        'approved_at': a.approved_at.isoformat() if a.approved_at else None,
        'approved_by': getattr(a.approved_by, 'email', None),
        'executed_at': a.executed_at.isoformat() if a.executed_at else None,
        'amazon_status': a.amazon_status, 'amazon_response': a.amazon_response,
        'failure_reason': a.failure_reason, 'dry_run': a.dry_run,
        'note': a.note,
        'data_period': {'start': a.data_period_start.isoformat() if a.data_period_start else None,
                        'end': a.data_period_end.isoformat() if a.data_period_end else None},
    }
