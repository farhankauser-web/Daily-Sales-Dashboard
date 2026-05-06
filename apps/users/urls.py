from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('login/',              views.login_view,         name='login'),
    path('logout/',             views.logout_view,        name='logout'),
    path('profile/',            views.profile,            name='profile'),
    path('manage/',             views.user_list,          name='list'),
    path('manage/create/',      views.user_create,        name='create'),
    path('manage/<int:pk>/',    views.user_edit,          name='edit'),
    path('manage/<int:pk>/toggle/', views.user_toggle_active, name='toggle'),
    path('roles/',              views.role_list,          name='roles'),
    path('roles/create/',       views.role_form,          name='role_create'),
    path('roles/<int:pk>/',     views.role_form,          name='role_edit'),
    path('audit/',              views.audit_log,          name='audit'),
]
