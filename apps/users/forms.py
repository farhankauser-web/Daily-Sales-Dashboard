"""
apps/users/forms.py
"""
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from .models import User, Role


def _marketplace_choices():
    """[(code, '🇺🇸 USA'), ...] from settings.AMAZON_MARKETPLACES."""
    flags = {'usa': '🇺🇸 USA', 'ca': '🇨🇦 CA', 'uk': '🇬🇧 UK',
              'de': '🇩🇪 DE', 'ae': '🇦🇪 UAE', 'sa': '🇸🇦 KSA'}
    out = []
    for code in (getattr(settings, 'AMAZON_MARKETPLACES', {}) or {}).keys():
        out.append((code, flags.get(code, code.upper())))
    if not out:
        # Fallback defaults if AMAZON_MARKETPLACES is empty
        out = list(flags.items())
    return out


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'autofocus': True, 'class': 'form-control',
                                       'placeholder': 'you@company.com'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control',
                                          'placeholder': '••••••••••'})
    )


class _AllowedMarketplacesMixin:
    """
    Replaces the raw JSONField with a CheckboxSelectMultiple so the user picks
    boxes instead of typing JSON. Empty selection = all marketplaces (matches
    `User.can_access_marketplace`'s "empty list = all" convention).
    """
    def _swap_allowed_marketplaces(self):
        # JSONField default `[]` makes the field "required" by default unless
        # we override it. Empty list is a legitimate value (= all), so it must
        # NOT be required.
        f = forms.MultipleChoiceField(
            choices=_marketplace_choices(),
            widget=forms.CheckboxSelectMultiple(
                attrs={'class': 'form-check-input'}),
            required=False,
            help_text='Pick the marketplaces this user can see. '
                      'Leave ALL boxes unchecked to grant access to every marketplace.',
        )
        # Pre-fill from the bound instance, if any
        if self.instance and self.instance.pk:
            f.initial = self.instance.allowed_marketplaces or []
        self.fields['allowed_marketplaces'] = f


class UserCreateForm(_AllowedMarketplacesMixin, forms.ModelForm):
    password1 = forms.CharField(label='Password',
                                 widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    password2 = forms.CharField(label='Confirm Password',
                                 widget=forms.PasswordInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role',
                  'is_active', 'allowed_marketplaces']
        widgets = {
            'email':       forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'role':        forms.Select(attrs={'class': 'form-select'}),
            'is_active':   forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._swap_allowed_marketplaces()
        # New users default to active
        self.fields['is_active'].initial = True

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password1'), cd.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        # CheckboxSelectMultiple gives us a list of strings — make sure it
        # lands in the model as a list (JSONField stores it verbatim).
        cd['allowed_marketplaces'] = list(cd.get('allowed_marketplaces') or [])
        return cd


class UserEditForm(_AllowedMarketplacesMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role',
                  'is_active', 'allowed_marketplaces', 'phone', 'timezone']
        widgets = {
            'email':      forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'role':       forms.Select(attrs={'class': 'form-select'}),
            'phone':      forms.TextInput(attrs={'class': 'form-control'}),
            'timezone':   forms.TextInput(attrs={'class': 'form-control'}),
            'is_active':  forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._swap_allowed_marketplaces()

    def clean(self):
        cd = super().clean()
        cd['allowed_marketplaces'] = list(cd.get('allowed_marketplaces') or [])
        return cd


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        exclude = ['created_at', 'updated_at']
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'form-control'}),
            'slug':        forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Auto-add Bootstrap checkbox classes to all BooleanFields
        for fname, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
