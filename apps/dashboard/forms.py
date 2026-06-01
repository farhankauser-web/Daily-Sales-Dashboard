"""
apps/dashboard/forms.py
"""
from django import forms
from .models import COGSEntry, MonthlyTarget, Product
import json


class COGSBulkUploadForm(forms.Form):
    """CSV upload for COGS. Accepts new and legacy formats."""
    csv_file = forms.FileField(
        label='CSV File',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
        help_text='Columns: SKU, ASIN, Region, Month, Cogs, FBA, ProductType, PackSize, Variant (all required in unified format; legacy format also supported)'
    )
    overwrite = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Overwrite existing COGS entries for same month'
    )


class FBARateBulkUploadForm(forms.Form):
    """CSV/Excel upload for FBA fulfilment fees with effective dates."""
    file = forms.FileField(
        label='File',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv,.xlsx'}),
        help_text='Columns: SKU, ASIN, Region, EffectiveFrom (YYYY-MM-DD), FBAFee. '
                  'One row per (SKU, EffectiveFrom). Each rate stays in effect until the next '
                  'effective date for the same SKU.'
    )
    overwrite = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Overwrite existing rate for the same (SKU, EffectiveFrom)'
    )


class COGSEntryForm(forms.ModelForm):
    class Meta:
        model = COGSEntry
        fields = ['product', 'month', 'unit_cost', 'shipping_cost',
                  'duties_cost', 'prep_cost', 'other_cost', 'notes']
        widgets = {
            'product':       forms.Select(attrs={'class': 'form-select'}),
            'month':         forms.DateInput(attrs={'class': 'form-control', 'type': 'month'}),
            'unit_cost':     forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'shipping_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'duties_cost':   forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'prep_cost':     forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'other_cost':    forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'notes':         forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class MonthlyTargetForm(forms.ModelForm):
    class Meta:
        model = MonthlyTarget
        fields = ['marketplace', 'month', 'revenue_target', 'units_target',
                  'tacos_target', 'gm_target', 'ppc_budget', 'notes']
        widgets = {
            'marketplace':    forms.Select(attrs={'class': 'form-select'}),
            'month':          forms.DateInput(attrs={'class': 'form-control', 'type': 'month'}),
            'revenue_target': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'}),
            'units_target':   forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0'}),
            'tacos_target':   forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '14.00'}),
            'gm_target':      forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '25.00'}),
            'ppc_budget':     forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'}),
            'notes':          forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        exclude = ['created_at', 'updated_at', 'updated_by']
        widgets = {
            'asin':              forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'B0XXXXXXXXX'}),
            'sku':               forms.TextInput(attrs={'class': 'form-control'}),
            'marketplace':       forms.Select(attrs={'class': 'form-select'}),
            'title':             forms.TextInput(attrs={'class': 'form-control'}),
            'category':          forms.TextInput(attrs={'class': 'form-control'}),
            'brand':             forms.TextInput(attrs={'class': 'form-control'}),
            'status':            forms.Select(attrs={'class': 'form-select'}),
            'weight_lbs':        forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'length_in':         forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'width_in':          forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'height_in':         forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'list_price':        forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'sale_price':        forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'fba_fee':           forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'referral_fee_pct':  forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }
