"""
apps/users/forms.py
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import User, Role


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


class UserCreateForm(forms.ModelForm):
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

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password1'), cd.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return cd


class UserEditForm(forms.ModelForm):
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
