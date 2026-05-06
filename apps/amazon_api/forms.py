"""apps/amazon_api/forms.py"""
from django import forms
from .models import AmazonAPIConfig, AnthropicConfig


class AmazonAPIConfigForm(forms.ModelForm):
    class Meta:
        model = AmazonAPIConfig
        exclude = ['created_by', 'updated_by', 'last_tested_at',
                   'last_test_status', 'last_test_detail', 'created_at', 'updated_at']
        widgets = {
            'marketplace':      forms.Select(attrs={'class': 'form-select'}),
            'label':            forms.TextInput(attrs={'class': 'form-control'}),
            'lwa_client_id':    forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'lwa_client_secret':forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'refresh_token':    forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'aws_access_key':   forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'aws_secret_key':   forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'aws_role_arn':     forms.TextInput(attrs={'class': 'form-control'}),
            'ads_client_id':    forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'ads_client_secret':forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'ads_refresh_token':forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'ads_profile_id':   forms.TextInput(attrs={'class': 'form-control'}),
            'seller_id':        forms.TextInput(attrs={'class': 'form-control'}),
            'marketplace_id':   forms.TextInput(attrs={'class': 'form-control'}),
            'is_active':        forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'lwa_client_id':     'From your SP-API Developer App (Login With Amazon)',
            'lwa_client_secret': 'LWA Client Secret — never share this',
            'refresh_token':     'Obtained during SP-API authorization flow',
            'aws_access_key':    'IAM user access key (optional, needed for some endpoints)',
        }


class AnthropicConfigForm(forms.ModelForm):
    class Meta:
        model = AnthropicConfig
        fields = ['label', 'api_key', 'model', 'is_active']
        widgets = {
            'label':   forms.TextInput(attrs={'class': 'form-control'}),
            'api_key': forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}, render_value=True),
            'model':   forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
