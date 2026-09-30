from django.urls import path
from .views import (
    StaffProfileView,
    CreateStaffView,
    AdminStaffDetailView,
    AdminStaffListView,
    StaffRolePermissionView,
)

urlpatterns = [
    path('profile/', StaffProfileView.as_view(), name='staff-profile'),
    path('role-permissions/', StaffRolePermissionView.as_view(), name='staff-role-permissions'),
    path('create/', CreateStaffView.as_view(), name='create-staff'),
    path('details/', AdminStaffDetailView.as_view(), name='admin-staff-detail'),
    path('list/', AdminStaffListView.as_view(), name='admin-staff-list'),
]
