"""
apps/users/models.py — Custom User + RBAC
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


# ── ROLES ─────────────────────────────────────────────────────────────────────
class Role(models.Model):
    """
    Named roles with a set of permission flags.
    Ships with: CEO, Operations Manager, Analyst, PPC Specialist, Viewer
    """
    ROLE_CHOICES = [
        ('ceo',           'CEO'),
        ('ops_manager',   'Operations Manager'),
        ('analyst',       'Analyst'),
        ('ppc_specialist','PPC Specialist'),
        ('viewer',        'Viewer'),
        ('custom',        'Custom'),
    ]

    name        = models.CharField(max_length=64, unique=True)
    slug        = models.CharField(max_length=32, choices=ROLE_CHOICES, default='custom')
    description = models.TextField(blank=True)

    # ── Granular permission flags ──────────────────────────────────────────────
    can_view_dashboard      = models.BooleanField(default=True)
    can_view_financials     = models.BooleanField(default=False)
    can_view_ppc            = models.BooleanField(default=False)
    can_view_inventory      = models.BooleanField(default=False)
    can_view_historical     = models.BooleanField(default=False)

    can_manage_cogs         = models.BooleanField(default=False)
    can_manage_targets      = models.BooleanField(default=False)
    can_manage_catalog      = models.BooleanField(default=False)

    can_configure_api       = models.BooleanField(default=False)   # SP-API / Ads API keys
    can_manage_users        = models.BooleanField(default=False)   # User management
    can_view_audit_log      = models.BooleanField(default=False)

    can_generate_ai_summary = models.BooleanField(default=False)   # Claude API calls

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_roles'
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def create_defaults(cls):
        """Seed the four default roles on first run."""
        defaults = [
            {
                'name': 'CEO', 'slug': 'ceo',
                'description': 'Full access to all features and data.',
                'can_view_dashboard': True,  'can_view_financials': True,
                'can_view_ppc': True,        'can_view_inventory': True,
                'can_view_historical': True, 'can_manage_cogs': True,
                'can_manage_targets': True,  'can_manage_catalog': True,
                'can_configure_api': True,   'can_manage_users': True,
                'can_view_audit_log': True,  'can_generate_ai_summary': True,
            },
            {
                'name': 'Operations Manager', 'slug': 'ops_manager',
                'description': 'Access to operations, inventory, and reporting.',
                'can_view_dashboard': True,  'can_view_financials': True,
                'can_view_ppc': True,        'can_view_inventory': True,
                'can_view_historical': True, 'can_manage_cogs': True,
                'can_manage_targets': True,  'can_manage_catalog': True,
                'can_generate_ai_summary': True,
            },
            {
                'name': 'PPC Specialist', 'slug': 'ppc_specialist',
                'description': 'Read/write access to PPC and advertising data.',
                'can_view_dashboard': True, 'can_view_ppc': True,
                'can_view_historical': True,
            },
            {
                'name': 'Analyst', 'slug': 'analyst',
                'description': 'Read-only analytical access.',
                'can_view_dashboard': True, 'can_view_financials': True,
                'can_view_ppc': True,       'can_view_inventory': True,
                'can_view_historical': True,'can_generate_ai_summary': True,
            },
            {
                'name': 'Viewer', 'slug': 'viewer',
                'description': 'Basic dashboard view only.',
                'can_view_dashboard': True,
            },
        ]
        for d in defaults:
            cls.objects.get_or_create(name=d['name'], defaults=d)


# ── USER MANAGER ──────────────────────────────────────────────────────────────
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra)


# ── CUSTOM USER ───────────────────────────────────────────────────────────────
class User(AbstractBaseUser, PermissionsMixin):
    email        = models.EmailField(unique=True)
    first_name   = models.CharField(max_length=64)
    last_name    = models.CharField(max_length=64)
    role         = models.ForeignKey(Role, on_delete=models.PROTECT,
                                     null=True, blank=True,
                                     related_name='users')
    is_active    = models.BooleanField(default=True)
    is_staff     = models.BooleanField(default=False)

    # Profile
    avatar       = models.ImageField(upload_to='avatars/', null=True, blank=True)
    timezone     = models.CharField(max_length=64, default='America/Los_Angeles')
    phone        = models.CharField(max_length=20, blank=True)

    # Audit
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    login_count  = models.PositiveIntegerField(default=0)

    # Marketplace access — CEO can see all; restrict others per need
    allowed_marketplaces = models.JSONField(
        default=list,
        help_text='Empty list = all marketplaces allowed'
    )

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = UserManager()

    class Meta:
        db_table = 'ix_users'
        ordering = ['email']

    def __str__(self):
        return f'{self.full_name} <{self.email}>'

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def has_perm_flag(self, flag: str) -> bool:
        """Check a role permission flag. Superusers always True."""
        if self.is_superuser:
            return True
        if not self.role:
            return False
        return getattr(self.role, flag, False)

    def can_access_marketplace(self, mp: str) -> bool:
        if self.is_superuser:
            return True
        if not self.allowed_marketplaces:
            return True          # empty = all
        return mp in self.allowed_marketplaces


# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('login', 'Login'), ('logout', 'Logout'),
        ('view', 'View'), ('create', 'Create'),
        ('update', 'Update'), ('delete', 'Delete'),
        ('api_call', 'API Call'), ('export', 'Export'),
        ('ai_summary', 'AI Summary Generated'),
    ]

    user       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   related_name='audit_logs')
    action     = models.CharField(max_length=32, choices=ACTION_CHOICES)
    resource   = models.CharField(max_length=128)
    detail     = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_audit_log'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f'{self.user} | {self.action} | {self.resource} | {self.created_at:%Y-%m-%d %H:%M}'
