"""
apps/users/views.py — Authentication + User Management
"""
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from .models import User, Role, AuditLog
from .forms import LoginForm, UserCreateForm, UserEditForm, RoleForm
from apps.core.decorators import permission_required


# ── AUTH ──────────────────────────────────────────────────────────────────────
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)

        # Update audit info
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        user.last_login_ip = ip.split(',')[0].strip() if ip else None
        user.login_count  += 1
        user.save(update_fields=['last_login_ip', 'login_count', 'last_login'])

        AuditLog.objects.create(
            user=user, action='login', resource='auth',
            ip_address=user.last_login_ip,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:256],
        )
        return redirect(request.GET.get('next', 'dashboard:index'))

    return render(request, 'users/login.html', {'form': form})


@require_POST
@login_required
def logout_view(request):
    AuditLog.objects.create(
        user=request.user, action='logout', resource='auth',
        ip_address=request.META.get('REMOTE_ADDR'),
    )
    logout(request)
    return redirect('users:login')


# ── USER LIST ─────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
def user_list(request):
    users = User.objects.select_related('role').order_by('email')
    return render(request, 'users/user_list.html', {'users': users})


# ── USER CREATE ───────────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.set_password(form.cleaned_data['password1'])
        user.save()
        AuditLog.objects.create(
            user=request.user, action='create',
            resource=f'user:{user.email}',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        messages.success(request, f'User {user.email} created successfully.')
        return redirect('users:list')
    return render(request, 'users/user_form.html', {'form': form, 'action': 'Create'})


# ── USER EDIT ─────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = UserEditForm(request.POST or None, instance=target)
    if request.method == 'POST' and form.is_valid():
        form.save()
        AuditLog.objects.create(
            user=request.user, action='update',
            resource=f'user:{target.email}',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        messages.success(request, f'User {target.email} updated.')
        return redirect('users:list')
    return render(request, 'users/user_form.html',
                  {'form': form, 'action': 'Edit', 'target': target})


# ── USER DEACTIVATE ───────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
@require_POST
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        return JsonResponse({'error': 'Cannot deactivate yourself.'}, status=400)
    target.is_active = not target.is_active
    target.save(update_fields=['is_active'])
    action = 'activate' if target.is_active else 'deactivate'
    AuditLog.objects.create(
        user=request.user, action='update',
        resource=f'user:{target.email}',
        detail=action,
        ip_address=request.META.get('REMOTE_ADDR'),
    )
    return JsonResponse({'is_active': target.is_active})


# ── ROLE LIST ─────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
def role_list(request):
    roles = Role.objects.prefetch_related('users').order_by('name')
    return render(request, 'users/role_list.html', {'roles': roles})


# ── ROLE CREATE / EDIT ────────────────────────────────────────────────────────
@login_required
@permission_required('can_manage_users')
def role_form(request, pk=None):
    instance = get_object_or_404(Role, pk=pk) if pk else None
    form = RoleForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        role = form.save()
        action = 'update' if pk else 'create'
        AuditLog.objects.create(
            user=request.user, action=action,
            resource=f'role:{role.name}',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        messages.success(request, f'Role "{role.name}" saved.')
        return redirect('users:roles')
    return render(request, 'users/role_form.html',
                  {'form': form, 'instance': instance})


# ── PROFILE ───────────────────────────────────────────────────────────────────
@login_required
def profile(request):
    pw_form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and pw_form.is_valid():
        pw_form.save()
        update_session_auth_hash(request, pw_form.user)
        messages.success(request, 'Password changed successfully.')
        return redirect('users:profile')
    return render(request, 'users/profile.html', {'pw_form': pw_form})


# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_view_audit_log')
def audit_log(request):
    logs = AuditLog.objects.select_related('user').order_by('-created_at')[:500]
    return render(request, 'users/audit_log.html', {'logs': logs})
