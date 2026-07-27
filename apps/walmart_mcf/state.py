"""
State machine for WalmartOrder with compare-and-swap transitions.

SQLite has no SELECT FOR UPDATE, so exclusivity comes from an atomic
UPDATE ... WHERE status IN (from_states): exactly one caller can win the
row-version race; everyone else gets rowcount 0 and must skip the order.
Every winning transition writes an AuditEvent in the same transaction.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, WalmartOrder, WalmartOrderState as S

# Legal forward edges. Anything not listed here is refused.
ALLOWED: dict[str, set[str]] = {
    S.NEW:               {S.VALIDATED, S.ERROR, S.HOLD, S.CANCELLED,
                          S.COMPLETED},   # COMPLETED = fulfilled outside the system
    S.VALIDATED:         {S.PROCESSING, S.ERROR, S.HOLD, S.CANCELLED},
    S.PROCESSING:        {S.MCF_CREATED, S.ERROR, S.NEW, S.CANCELLED},   # NEW = safe rollback on clean failure
    S.MCF_CREATED:       {S.SHIPPED, S.CANCELLED, S.ERROR},
    S.SHIPPED:           {S.TRACKING_UPLOADED, S.CANCELLED, S.ERROR},
    S.TRACKING_UPLOADED: {S.COMPLETED, S.SHIPPED, S.CANCELLED},          # SHIPPED = new packages appeared
    S.HOLD:              {S.HOLD, S.NEW, S.VALIDATED, S.ERROR, S.CANCELLED},  # HOLD→HOLD = still short
    S.ERROR:             {S.NEW, S.CANCELLED},               # admin retry / cancel
    S.CANCELLED:         {S.NEW},                           # admin decides to resubmit
    S.COMPLETED:         set(),
}


class IllegalTransition(Exception):
    pass


def transition(order: WalmartOrder, to_state: str, actor: str,
               detail: dict | None = None,
               from_states: list[str] | None = None,
               error_reason: str | None = None) -> bool:
    """
    Atomically move `order` to `to_state`. Returns True if this caller won
    the transition, False if another worker already moved the order on
    (or it is no longer in an expected source state).
    """
    src = from_states if from_states is not None else [order.status]
    for s in src:
        if to_state not in ALLOWED.get(s, set()):
            raise IllegalTransition(f'{s} → {to_state} is not allowed')

    fields = {'status': to_state, 'updated_at': timezone.now()}
    if error_reason is not None:
        fields['error_reason'] = error_reason

    with transaction.atomic():
        updated = (WalmartOrder.objects
                   .filter(pk=order.pk, status__in=src)
                   .update(**fields))
        if not updated:
            return False
        AuditEvent.objects.create(
            order_id=order.pk,
            from_state=order.status,
            to_state=to_state,
            actor=actor,
            detail=detail or {},
        )
    order.status = to_state
    if error_reason is not None:
        order.error_reason = error_reason
    return True
