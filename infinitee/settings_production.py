"""
infinitee/settings_production.py

Usage: DJANGO_SETTINGS_MODULE=infinitee.settings_production

Extends base settings with production hardening.
"""
from .settings import *   # noqa

# ── Override for production ────────────────────────────────────────────────────
DEBUG = False

# Strict security
SECURE_SSL_REDIRECT          = True
SECURE_HSTS_SECONDS          = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD          = True
SECURE_PROXY_SSL_HEADER      = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE        = True
CSRF_COOKIE_SECURE           = True

# Disable debug toolbar etc. in prod
INSTALLED_APPS = [a for a in INSTALLED_APPS if 'debug' not in a.lower()]

# Faster password hashing in production (default Argon2 or bcrypt)
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

# Logging: production — errors to file only
LOGGING['handlers']['console']['level'] = 'ERROR'
LOGGING['root']['level'] = 'WARNING'
