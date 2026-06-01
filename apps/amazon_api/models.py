"""
apps/amazon_api/models.py — Encrypted API credentials storage
"""
from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
import base64


def _cipher():
    key = settings.FIELD_ENCRYPTION_KEY
    if isinstance(key, str):
        key = key.encode()
    # Ensure key is valid base64-encoded 32-byte Fernet key
    try:
        return Fernet(key)
    except Exception:
        # Fallback to a derived key for dev (NEVER in production)
        import hashlib
        k = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
        return Fernet(k)


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ''
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ''
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except Exception:
        return ''


class EncryptedField(models.TextField):
    """Custom field that encrypts on save and decrypts on read."""

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_value(value)

    def to_python(self, value):
        if isinstance(value, str) and value.startswith('gAAAA'):
            return decrypt_value(value)
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        # Don't double-encrypt
        if value.startswith('gAAAA'):
            return value
        return encrypt_value(value)


# ── SP-API CREDENTIAL SET ─────────────────────────────────────────────────────
class AmazonAPIConfig(models.Model):
    """
    Stores one credential set per Amazon marketplace account.
    All sensitive fields are stored encrypted (Fernet AES-128-CBC).
    """
    MARKETPLACE_CHOICES = [
        ('usa', '🇺🇸 United States'),
        ('ca',  '🇨🇦 Canada'),
        ('uk',  '🇬🇧 United Kingdom'),
        ('de',  '🇩🇪 Germany'),
        ('ae',  '🇦🇪 UAE'),
        ('sa',  '🇸🇦 Saudi Arabia'),
    ]

    marketplace     = models.CharField(max_length=8, choices=MARKETPLACE_CHOICES, unique=True)
    label           = models.CharField(max_length=64, help_text='Friendly name, e.g. "USA Main"')
    is_active       = models.BooleanField(default=True)

    # SP-API OAuth (LWA)
    lwa_client_id     = EncryptedField(blank=True, help_text='Login With Amazon Client ID')
    lwa_client_secret = EncryptedField(blank=True, help_text='Login With Amazon Client Secret')
    refresh_token     = EncryptedField(blank=True, help_text='SP-API Refresh Token')

    # AWS IAM (required for some SP-API calls)
    aws_access_key    = EncryptedField(blank=True)
    aws_secret_key    = EncryptedField(blank=True)
    aws_role_arn      = EncryptedField(blank=True, help_text='IAM Role ARN for SP-API')

    # Ads API
    ads_client_id     = EncryptedField(blank=True, help_text='Advertising API Client ID')
    ads_client_secret = EncryptedField(blank=True)
    ads_refresh_token = EncryptedField(blank=True)
    ads_profile_id    = models.CharField(max_length=32, blank=True,
                                         help_text='Advertising profile ID (plain text)')

    # Metadata
    seller_id       = models.CharField(max_length=32, blank=True)
    marketplace_id  = models.CharField(max_length=32, blank=True,
                                       help_text='Amazon marketplace ID, e.g. ATVPDKIKX0DER')

    # Connection health
    last_tested_at   = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(max_length=16, default='untested',
                                        choices=[('ok','OK'),('error','Error'),('untested','Untested')])
    last_test_detail = models.TextField(blank=True)

    created_by  = models.ForeignKey('users.User', on_delete=models.SET_NULL,
                                    null=True, related_name='api_configs_created')
    updated_by  = models.ForeignKey('users.User', on_delete=models.SET_NULL,
                                    null=True, related_name='api_configs_updated')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_amazon_api_config'
        verbose_name = 'Amazon API Configuration'
        verbose_name_plural = 'Amazon API Configurations'

    def __str__(self):
        return f'{self.get_marketplace_display()} — {self.label}'

    def has_sp_api_credentials(self):
        return bool(self.lwa_client_id and self.lwa_client_secret and self.refresh_token)

    def has_ads_credentials(self):
        return bool(self.ads_client_id and self.ads_client_secret and self.ads_refresh_token)


# ── ANTHROPIC CONFIG (legacy — kept for backward compat with summary_stream) ──
class AnthropicConfig(models.Model):
    """
    Legacy model. New code should use AIProviderConfig(provider='anthropic').
    Kept so existing rows and the summary_stream fallback path continue to work.
    """
    label      = models.CharField(max_length=64, default='Primary')
    api_key    = EncryptedField(help_text='Anthropic API Key (sk-ant-...)')
    model      = models.CharField(max_length=64, default='claude-sonnet-4-20250514')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_anthropic_config'

    def __str__(self):
        return f'{self.label} ({self.model})'

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()


# ── AI PROVIDER CONFIG (Anthropic / OpenAI / Gemini) ─────────────────────────
class AIProviderConfig(models.Model):
    """
    Unified storage for all AI provider API keys.
    One row per provider; encrypted at rest via EncryptedField.
    """
    PROVIDER_CHOICES = [
        ('anthropic', 'Anthropic (Claude)'),
        ('openai',    'OpenAI (ChatGPT)'),
        ('gemini',    'Google (Gemini)'),
    ]
    DEFAULT_MODELS = {
        'anthropic': 'claude-sonnet-4-20250514',
        'openai':    'gpt-4o',
        'gemini':    'gemini-2.0-flash',
    }
    PROVIDER_META = {
        'anthropic': {
            'icon': '✨',
            'color': 'linear-gradient(135deg,#f59e0b,#f97316)',
            'hint': 'Starts with sk-ant-api03-…',
            'docs_url': 'https://console.anthropic.com',
            'docs_label': 'console.anthropic.com',
            'purpose': 'Powers AI Executive Summaries on the dashboard.',
        },
        'openai': {
            'icon': '🤖',
            'color': 'linear-gradient(135deg,#10b981,#059669)',
            'hint': 'Starts with sk-proj-… or sk-…',
            'docs_url': 'https://platform.openai.com/api-keys',
            'docs_label': 'platform.openai.com',
            'purpose': 'OpenAI GPT models for future AI features.',
        },
        'gemini': {
            'icon': '💎',
            'color': 'linear-gradient(135deg,#3b82f6,#8b5cf6)',
            'hint': 'Starts with AIza…',
            'docs_url': 'https://aistudio.google.com/app/apikey',
            'docs_label': 'aistudio.google.com',
            'purpose': 'Google Gemini models for future AI features.',
        },
    }

    provider   = models.CharField(max_length=16, choices=PROVIDER_CHOICES, unique=True)
    label      = models.CharField(max_length=64, default='Primary')
    api_key    = EncryptedField(help_text='API Key (encrypted at rest)')
    model      = models.CharField(max_length=64, default='')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_ai_provider_config'

    def __str__(self):
        return f'{self.get_provider_display()} — {self.model or "no model set"}'

    @classmethod
    def get_for(cls, provider: str):
        """Return the active config for a given provider, or None."""
        return cls.objects.filter(provider=provider, is_active=True).first()

    def get_model(self):
        return self.model or self.DEFAULT_MODELS.get(self.provider, '')

    def meta(self):
        return self.PROVIDER_META.get(self.provider, {})


# ── SYNC LOG ──────────────────────────────────────────────────────────────────
class APISyncLog(models.Model):
    STATUS_CHOICES = [('ok','OK'),('error','Error'),('partial','Partial')]

    config      = models.ForeignKey(AmazonAPIConfig, on_delete=models.CASCADE,
                                    related_name='sync_logs')
    data_type   = models.CharField(max_length=32,
                                   help_text='e.g. orders, sales_traffic, campaigns')
    date_range  = models.CharField(max_length=32, blank=True)
    status      = models.CharField(max_length=16, choices=STATUS_CHOICES)
    records     = models.IntegerField(default=0)
    error_msg   = models.TextField(blank=True)
    duration_ms = models.IntegerField(default=0)
    triggered_by= models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_api_sync_log'
        ordering = ['-created_at']
