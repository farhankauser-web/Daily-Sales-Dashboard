"""
Infinitee Xclusives — Django Settings
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-insecure-key-CHANGE-BEFORE-PRODUCTION!!!')
DEBUG      = os.environ.get('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'crispy_forms',
    'crispy_bootstrap5',
    'apps.core',
    'apps.users',
    'apps.dashboard',
    'apps.amazon_api',
    'apps.sqp',
    'apps.walmart_mcf',
    'apps.atlas',
    'apps.inventory_planning',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # serve static under DEBUG=False
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.RBACMiddleware',
    'apps.core.middleware.AuditLogMiddleware',
]

ROOT_URLCONF = 'infinitee.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
            'apps.core.context_processors.global_context',
        ],
        'libraries': {
            'ix_tags': 'apps.core.templatetags.ix_tags',
        },
    },
}]

WSGI_APPLICATION = 'infinitee.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
# Local dev defaults to SQLite (zero-config). Production (EC2) sets DATABASE_URL
# to a Postgres DSN, e.g.
#     DATABASE_URL=postgres://user:pass@host:5432/infinitee?sslmode=require
# and installs psycopg2-binary. Nothing else changes — see deploy/DATABASE.md
# for the SQLite → Postgres cutover runbook.
def _database_from_env():
    import urllib.parse as _url
    dsn = os.environ.get('DATABASE_URL', '').strip()
    if not dsn:
        return {'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3'}
    p = _url.urlparse(dsn)
    scheme = p.scheme.split('+')[0]
    engine = {'postgres': 'django.db.backends.postgresql',
              'postgresql': 'django.db.backends.postgresql',
              'sqlite': 'django.db.backends.sqlite3'}.get(scheme)
    if not engine:
        raise ValueError(f'Unsupported DATABASE_URL scheme: {scheme!r}')
    if engine.endswith('sqlite3'):
        return {'ENGINE': engine, 'NAME': p.path or str(BASE_DIR / 'db.sqlite3')}
    cfg = {
        'ENGINE':       engine,
        'NAME':         _url.unquote((p.path or '').lstrip('/')),
        'USER':         _url.unquote(p.username or ''),
        'PASSWORD':     _url.unquote(p.password or ''),
        'HOST':         p.hostname or '',
        'PORT':         str(p.port or ''),
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
    }
    opts = {k: v[0] for k, v in _url.parse_qs(p.query).items()}
    if opts:                                   # e.g. ?sslmode=require
        cfg['OPTIONS'] = opts
    return cfg


DATABASES = {'default': _database_from_env()}

AUTH_USER_MODEL = 'users.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL           = '/auth/login/'
LOGIN_REDIRECT_URL  = '/dashboard/'
LOGOUT_REDIRECT_URL = '/auth/login/'

SESSION_COOKIE_HTTPONLY   = True
SESSION_COOKIE_SECURE     = not DEBUG
SESSION_COOKIE_AGE        = 28800
CSRF_COOKIE_SECURE        = not DEBUG
# NOT HttpOnly: the dashboard's fetch() buttons read the CSRF token from
# document.cookie. Django docs: hiding the CSRF cookie from JS provides no
# practical security benefit (the token is intentionally non-secret; the
# session cookie above stays HttpOnly).
CSRF_COOKIE_HTTPONLY      = False
X_FRAME_OPTIONS           = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY      = 'same-origin'

# ── HTTPS / reverse-proxy hardening (VAPT H1/H2/M3) ────────────────────────
# TLS terminates upstream (nginx+Let's Encrypt on the box, or Cloudflare/LB);
# traffic reaches Django over HTTP carrying X-Forwarded-Proto. Trust it so
# request.is_secure()/redirects/Secure-cookies behave correctly behind the proxy.
# All flags are env-gated so nothing activates until the proxy is live and the
# env vars are set on deploy (avoids redirect loops / lockouts before then).
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get(
    'CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]   # e.g. https://dashboard.infinitee.biz
SECURE_SSL_REDIRECT            = os.environ.get('SECURE_SSL_REDIRECT', 'False') == 'True'
SECURE_HSTS_SECONDS            = int(os.environ.get('SECURE_HSTS_SECONDS', '0') or '0')
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD            = SECURE_HSTS_SECONDS > 0

FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', 'CHANGE-ME-generate-fernet-key=')
ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL      = 'claude-sonnet-4-20250514'

STATIC_URL       = '/static/'
STATICFILES_DIRS = [d for d in [BASE_DIR / 'static'] if d.exists()]  # avoid W004 when absent
STATIC_ROOT      = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Non-manifest: compresses + long-cache headers, but won't hard-fail on a
    # missing {% static %} reference (safer for the first prod cutover). Switch
    # to CompressedManifestStaticFilesStorage later for hashed cache-busting.
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}
MEDIA_URL        = '/media/'
MEDIA_ROOT       = BASE_DIR / 'media'

LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'America/Los_Angeles'
USE_I18N      = True
USE_TZ        = True

DEFAULT_AUTO_FIELD          = 'django.db.models.BigAutoField'
CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK          = 'bootstrap5'

AMAZON_MARKETPLACES = {
    'usa': {'id': 'ATVPDKIKX0DER',  'region': 'us-east-1', 'endpoint': 'https://sellingpartnerapi-na.amazon.com', 'timezone': 'America/Los_Angeles', 'currency': 'USD'},
    'ca':  {'id': 'A2EUQ1WTGCTBG2', 'region': 'us-east-1', 'endpoint': 'https://sellingpartnerapi-na.amazon.com', 'timezone': 'America/Toronto', 'currency': 'CAD'},
    'uk':  {'id': 'A1F83G8C2ARO7P', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Europe/London', 'currency': 'GBP', 'vat': 0.20},
    'de':  {'id': 'A1PA6795UKMFR9', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Europe/Berlin', 'currency': 'EUR', 'vat': 0.19},
    'ae':  {'id': 'A2VIGQ35RCS4UG', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Asia/Dubai', 'currency': 'AED', 'vat': 0.05},
    'sa':  {'id': 'A17E79C6D8DWNP', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Asia/Riyadh', 'currency': 'SAR', 'vat': 0.15},
}

# ── Amazon Marketing Stream (AMS) — S3 destination per marketplace ─────────
# Firehose for each marketplace writes its events into a region-specific bucket.
# AWS region MUST match Amazon's published regions for the Ads region:
#   NA advertisers → us-east-1
#   EU advertisers → eu-west-1
#   FE advertisers → us-west-2
AMS_S3 = {
    'usa': {'region': 'us-east-1', 'bucket': 'amzn-usa-data-stream-bucket',
            'prefix': ''},
    # add 'ca', 'uk', 'de', 'ae', 'sa' here as their buckets come online
}

# ── AWS credentials (read-only S3 access used by ingest_ams_s3) ────────────
# Set in environment, never commit. The IAM user behind this only needs:
#   s3:GetObject + s3:ListBucket on the AMS buckets above.
AWS_ACCESS_KEY_ID     = os.environ.get('AWS_ACCESS_KEY_ID',     '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')

# ── Walmart Marketplace → Amazon MCF automation ────────────────────────────
WALMART_API_BASE      = os.environ.get('WALMART_API_BASE',
                                       'https://marketplace.walmartapis.com')
WALMART_CLIENT_ID     = os.environ.get('WALMART_CLIENT_ID', '')
WALMART_CLIENT_SECRET = os.environ.get('WALMART_CLIENT_SECRET', '')
WALMART_SVC_NAME      = os.environ.get('WALMART_SVC_NAME', 'Walmart Marketplace')
# Packaging compliance: BLANK_BOX keeps Amazon branding off the box;
# BLOCK_AMZL keeps Amazon Logistics (TBA tracking Walmart can't track) off
# the order. 'Required' | 'NotRequired'. Order-level values override the
# Seller Central defaults.
WALMART_MCF_FEATURES  = {
    'BLANK_BOX':  os.environ.get('WALMART_MCF_BLANK_BOX',  'Required'),
    'BLOCK_AMZL': os.environ.get('WALMART_MCF_BLOCK_AMZL', 'Required'),
}
WALMART_MCF_MARKETPLACE   = os.environ.get('WALMART_MCF_MARKETPLACE', 'usa')
WALMART_MCF_ALERT_EMAILS  = [e for e in os.environ.get(
    'WALMART_MCF_ALERT_EMAILS', '').split(',') if e.strip()]

CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'verbose': {'format': '{levelname} {asctime} {module} — {message}', 'style': '{'}},
    'handlers': {
        'file':    {'level': 'INFO', 'class': 'logging.handlers.RotatingFileHandler',
                    'filename': str(LOGS_DIR / 'infinitee.log'), 'maxBytes': 10*1024*1024,
                    'backupCount': 5, 'formatter': 'verbose'},
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
}
