from django.urls import path
from .views import (
    AdminBulkAssignTaskView,
    AdminTaskManagementView,
    AdminTaskManagementPaginatedView,
    StaffTaskOperationView,
    AdminRecurringScheduleView,
    StaffSimpleDashboardView
)

urlpatterns = [
    # Admin
    path('admin/assign-bulk/', AdminBulkAssignTaskView.as_view()),
    path('admin/manage/', AdminTaskManagementView.as_view()),
    path('admin/manage/paginated/', AdminTaskManagementPaginatedView.as_view()),

    # Staff
    path('staff/operations/', StaffTaskOperationView.as_view()),
    path('admin/recurring-schedule/', AdminRecurringScheduleView.as_view()),

    #staff work
    path('dashboard/simple-stats/', StaffSimpleDashboardView.as_view(), name='staff-simple-dashboard'),
]
