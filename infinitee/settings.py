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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME':   BASE_DIR / 'db.sqlite3',
    }
}

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
CSRF_COOKIE_HTTPONLY      = True
X_FRAME_OPTIONS           = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True

FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', 'CHANGE-ME-generate-fernet-key=')
ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL      = 'claude-sonnet-4-20250514'

STATIC_URL       = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT      = BASE_DIR / 'staticfiles'
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
    'usa': {'id': 'ATVPDKIKX0DER',  'region': 'us-east-1', 'endpoint': 'https://sellingpartnerapi-na.amazon.com', 'timezone': 'America/Los_Angeles'},
    'ca':  {'id': 'A2EUQ1WTGCTBG2', 'region': 'us-east-1', 'endpoint': 'https://sellingpartnerapi-na.amazon.com', 'timezone': 'America/Toronto'},
    'uk':  {'id': 'A1F83G8C2ARO7P', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Europe/London'},
    'de':  {'id': 'A1PA6795UKMFR9', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Europe/Berlin'},
    'ae':  {'id': 'A2VIGQ35RCS4UG', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Asia/Dubai'},
    'sa':  {'id': 'A17E79C6D8DWNP', 'region': 'eu-west-1', 'endpoint': 'https://sellingpartnerapi-eu.amazon.com', 'timezone': 'Asia/Riyadh'},
}

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
