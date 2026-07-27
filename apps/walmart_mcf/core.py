"""
Shared infrastructure: job locks, API logging, retry with backoff,
admin notifications.
"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from contextlib import contextmanager

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_MAX_LOG_BODY = 4000
_REDACT_KEYS = {'access_token', 'refresh_token', 'client_secret', 'authorization',
                'wm_sec.access_token', 'x-amz-access-token', 'password'}


# ── Job lock (prevents overlapping cron runs of the same command) ────────────

def _lock_file(fh) -> None:
    """Acquire a non-blocking exclusive lock (Unix: fcntl, Windows: msvcrt)."""
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh) -> None:
    if os.name == 'nt':
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def job_lock(name: str):
    """Cross-platform lock; raises JobAlreadyRunning if another run holds it."""
    # Windows has no /tmp + no fcntl — use project logs dir + OS-specific lock
    lock_dir = getattr(settings, 'WALMART_MCF_LOCK_DIR', None) or os.path.join(
        getattr(settings, 'BASE_DIR', os.getcwd()), 'logs'
    )
    os.makedirs(lock_dir, exist_ok=True)
    path = os.path.join(lock_dir, f'wm_mcf_{name}.lock')
    fh = open(path, 'w')
    try:
        _lock_file(fh)
    except (BlockingIOError, OSError) as e:
        fh.close()
        raise JobAlreadyRunning(name) from e
    try:
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        _unlock_file(fh)
        fh.close()


class JobAlreadyRunning(Exception):
    pass


# ── API logging ──────────────────────────────────────────────────────────────

def _redact(obj):
    if isinstance(obj, dict):
        return {k: ('***' if k.lower() in _REDACT_KEYS else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _dump(body) -> str:
    if body is None:
        return ''
    try:
        if isinstance(body, (dict, list)):
            s = json.dumps(_redact(body), default=str)
        else:
            s = str(body)
    except Exception:
        s = repr(body)
    return s[:_MAX_LOG_BODY]


def log_api(direction: str, endpoint: str, method: str,
            request_body=None, response_body=None,
            status_code: int | None = None, duration_ms: int = 0,
            correlation_id: str = '') -> None:
    from .models import APILog
    try:
        APILog.objects.create(
            direction=direction, endpoint=endpoint[:256], method=method,
            request_body=_dump(request_body), response_body=_dump(response_body),
            status_code=status_code, duration_ms=duration_ms,
            correlation_id=correlation_id[:64],
        )
    except Exception:                       # logging must never break the pipeline
        logger.exception('APILog write failed for %s %s', method, endpoint)


def log_error(exc: Exception, endpoint: str = '', order=None,
              retry_count: int = 0) -> None:
    from .models import ErrorLog
    try:
        ErrorLog.objects.create(
            order=order, endpoint=endpoint[:256],
            exception=f'{type(exc).__name__}: {exc}'[:256],
            stack_trace=traceback.format_exc(),
            retry_count=retry_count,
        )
    except Exception:
        logger.exception('ErrorLog write failed')


# ── Retry with exponential backoff ───────────────────────────────────────────

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FATAL_STATUS = {400, 401, 403, 404, 422}   # never retried


class FatalAPIError(Exception):
    """4xx-class failure — do not retry; route the order to ERROR."""
    def __init__(self, message: str, status_code: int | None = None,
                 body: str = ''):
        super().__init__(message)
        self.status_code = status_code
        self.body = body[:1000]


def request_with_retry(fn, *, endpoint: str = '', max_tries: int = 5,
                       base_delay: float = 1.0):
    """
    Call fn() (a zero-arg closure doing one HTTP request and returning the
    requests.Response). Retries 429/5xx/network errors with exponential
    backoff (1,2,4,8,16s + up to 30s cap). Raises FatalAPIError on 4xx.
    """
    last_exc: Exception | None = None
    for attempt in range(max_tries):
        try:
            resp = fn()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            log_error(exc, endpoint=endpoint, retry_count=attempt)
            time.sleep(min(base_delay * (2 ** attempt), 30))
            continue
        if resp.status_code in RETRYABLE_STATUS:
            last_exc = requests.HTTPError(f'HTTP {resp.status_code}', response=resp)
            if attempt < max_tries - 1:
                retry_after = resp.headers.get('Retry-After')
                delay = (float(retry_after) if retry_after and retry_after.isdigit()
                         else min(base_delay * (2 ** attempt), 30))
                time.sleep(delay)
                continue
            break
        if resp.status_code in FATAL_STATUS or 400 <= resp.status_code < 500:
            raise FatalAPIError(
                f'HTTP {resp.status_code} from {endpoint}',
                status_code=resp.status_code, body=resp.text or '')
        return resp
    raise last_exc or RuntimeError(f'request_with_retry exhausted for {endpoint}')


def new_correlation_id() -> str:
    return uuid.uuid4().hex


# ── Admin notifications ──────────────────────────────────────────────────────

def notify_admin(subject: str, body: str) -> None:
    """
    Email the configured admins (WALMART_MCF_ALERT_EMAILS or settings.ADMINS).
    Falls back to a WARNING log line when email isn't configured — a
    notification failure must never break order processing.
    """
    recipients = getattr(settings, 'WALMART_MCF_ALERT_EMAILS', None) or \
        [a[1] for a in getattr(settings, 'ADMINS', [])]
    logger.warning('[WM-MCF ALERT] %s — %s', subject, body[:500])
    if not recipients or not getattr(settings, 'EMAIL_HOST', ''):
        return
    try:
        from django.core.mail import send_mail
        send_mail(f'[Walmart-MCF] {subject}', body,
                  getattr(settings, 'DEFAULT_FROM_EMAIL', 'alerts@infinitee.biz'),
                  recipients, fail_silently=True)
    except Exception:
        logger.exception('notify_admin email failed')
