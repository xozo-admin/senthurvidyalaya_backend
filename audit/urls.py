# audit/urls.py
from django.urls import path
from .views import (
    AuditLogListView,
    AuditLogDetailView,
    AuditLogSummaryView,
    AuditLogExportView,
    StaffPaymentAuditLogListView,
    StaffPaymentAuditLogDetailView,
    StaffPaymentAuditLogSummaryView,
    StaffPaymentAuditLogExportView,
    StaffSalaryPaymentAuditLogListView,
    StaffFeesPaymentAuditLogListView,
)

urlpatterns = [
    # Admin endpoints: full audit visibility
    path('admin/logs/', AuditLogListView.as_view(), name='audit-admin-log-list'),
    path('admin/logs/summary/', AuditLogSummaryView.as_view(), name='audit-admin-log-summary'),
    path('admin/logs/export/', AuditLogExportView.as_view(), name='audit-admin-log-export'),
    path('admin/logs/<int:log_id>/', AuditLogDetailView.as_view(), name='audit-admin-log-detail'),

    # Staff endpoints (admin_staff/finance_staff): payment logs only
    path('staff/payment-logs/', StaffPaymentAuditLogListView.as_view(), name='audit-staff-payment-log-list'),
    path('staff/payment-logs/summary/', StaffPaymentAuditLogSummaryView.as_view(), name='audit-staff-payment-log-summary'),
    path('staff/payment-logs/export/', StaffPaymentAuditLogExportView.as_view(), name='audit-staff-payment-log-export'),
    path('staff/payment-logs/<int:log_id>/', StaffPaymentAuditLogDetailView.as_view(), name='audit-staff-payment-log-detail'),
    path('staff/payment-logs/salary/', StaffSalaryPaymentAuditLogListView.as_view(), name='audit-staff-salary-payment-log-list'),
    path('staff/payment-logs/fees/', StaffFeesPaymentAuditLogListView.as_view(), name='audit-staff-fees-payment-log-list'),
]
