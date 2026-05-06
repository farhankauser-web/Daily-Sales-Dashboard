"""
apps/core/templatetags/ix_tags.py
"""
from django import template

register = template.Library()


@register.filter
def has_perm(user, flag):
    """{{ user|has_perm:'can_view_ppc' }}"""
    if not user or not user.is_authenticated:
        return False
    return user.has_perm_flag(flag)


@register.filter
def can_access_mp(user, marketplace):
    """{{ user|can_access_mp:'usa' }}"""
    if not user or not user.is_authenticated:
        return False
    return user.can_access_marketplace(marketplace)


@register.filter
def get_marketplaces(targets_list):
    """
    Returns a set of marketplace codes from a list of MonthlyTarget objects.
    Usage: {% with set_mps=month_targets|get_marketplaces %}
    """
    return {t.marketplace for t in targets_list}


@register.simple_tag
def marketplace_choices():
    return [
        ('usa', 'United States', '🇺🇸'),
        ('ca',  'Canada',        '🇨🇦'),
        ('uk',  'United Kingdom','🇬🇧'),
        ('de',  'Germany',       '🇩🇪'),
        ('ae',  'UAE',           '🇦🇪'),
        ('sa',  'Saudi Arabia',  '🇸🇦'),
    ]


@register.filter
def get_item(dictionary, key):
    """{{ my_dict|get_item:some_key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(str(key))
    return None
