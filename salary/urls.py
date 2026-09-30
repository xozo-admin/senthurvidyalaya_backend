# salary/urls.py

from django.urls import path
from .views import (
    # Existing views
    AdminSalarySettingsView, 
    AdminTeacherSalarySettingsView,
    MySalaryDashboardView, 
    MyTeacherSalaryDashboardView,
    
    # New payment views
    AdminStaffSalaryPaymentListView,
    AdminStaffSalaryPaymentDetailView,
    AdminProcessStaffSalaryView,
    AdminBulkProcessStaffSalaryView,
    AdminTeacherSalaryPaymentListView,
    AdminTeacherSalaryPaymentDetailView,
    AdminProcessTeacherSalaryView,
    AdminBulkProcessTeacherSalaryView,
    AdminSalarySummaryView,
    AdminSalaryCardsOverviewView,
    AdminEmployeeSalaryYearlyReportView,
    
    # Bank transfer enabled payment processing
    AdminProcessStaffSalaryWithBankView,
    AdminProcessTeacherSalaryWithBankView,      # <-- Now defined
    AdminBulkProcessSalaryWithBankView,
    AdminBulkProcessTeacherSalaryWithBankView,  # <-- Now defined
    AdminProcessExistingStaffSalaryWithBankView,
    AdminProcessExistingTeacherSalaryWithBankView,
    AdminRetryStaffSalaryWithBankView,
    AdminRetryTeacherSalaryWithBankView,
    AdminSalaryAuditLogsView,
    AdminVerifyBankTransferView,
    AdminSalaryTransferOtpSendView,
    AdminSalaryTransferOtpResendView,
    AdminSalaryTransferOtpVerifyView,
    AdminStaffSalaryMobileListView,
    AdminTeacherSalaryMobileListView
)

urlpatterns = [
    # ==========================
    # 1. ADMIN SETTINGS (EXISTING)
    # ==========================
    path('admin/structure/staff/', AdminSalarySettingsView.as_view()),
    path('admin/structure/teacher/', AdminTeacherSalarySettingsView.as_view()),

    # ==========================
    # 2. DASHBOARDS (EXISTING)
    # ==========================
    path('dashboard/staff/', MySalaryDashboardView.as_view()),
    path('dashboard/teacher/', MyTeacherSalaryDashboardView.as_view()),

    # ==========================
    # 3. ADMIN PAYMENT MANAGEMENT
    # ==========================
    
    # Staff Payments
    path('admin/payments/staff/', AdminStaffSalaryPaymentListView.as_view()),
    path('admin/payments/staff/process/', AdminProcessStaffSalaryView.as_view()),
    path('admin/payments/staff/bulk-process/', AdminBulkProcessStaffSalaryView.as_view()),
    path('admin/payments/staff/<int:payment_id>/', AdminStaffSalaryPaymentDetailView.as_view()),
    
    # Teacher Payments
    path('admin/payments/teacher/', AdminTeacherSalaryPaymentListView.as_view()),
    path('admin/payments/teacher/process/', AdminProcessTeacherSalaryView.as_view()),
    path('admin/payments/teacher/bulk-process/', AdminBulkProcessTeacherSalaryView.as_view()),
    path('admin/payments/teacher/<int:payment_id>/', AdminTeacherSalaryPaymentDetailView.as_view()),
    
    # Summary
    path('admin/payments/summary/', AdminSalarySummaryView.as_view()),
    path('admin/payments/employee-yearly-report/', AdminEmployeeSalaryYearlyReportView.as_view()),
    path('admin/cards/overview/', AdminSalaryCardsOverviewView.as_view()),
    path('admin/audit/logs/', AdminSalaryAuditLogsView.as_view()),

    # ==========================
    # 4. BANK TRANSFER ENABLED PAYMENTS
    # ==========================
    
    # Staff Bank Transfer
    path('admin/payments/staff/process-with-bank/', AdminProcessStaffSalaryWithBankView.as_view()),
    path('admin/payments/staff/bulk-process-with-bank/', AdminBulkProcessSalaryWithBankView.as_view()),
    path('admin/payments/staff/<int:payment_id>/process-with-bank/', AdminProcessExistingStaffSalaryWithBankView.as_view()),
    
    # Teacher Bank Transfer
    path('admin/payments/teacher/process-with-bank/', AdminProcessTeacherSalaryWithBankView.as_view()),
    path('admin/payments/teacher/bulk-process-with-bank/', AdminBulkProcessTeacherSalaryWithBankView.as_view()),
    path('admin/payments/teacher/<int:payment_id>/process-with-bank/', AdminProcessExistingTeacherSalaryWithBankView.as_view()),

    # Retry failed transfers
    path('admin/payments/staff/<int:payment_id>/retry-with-bank/', AdminRetryStaffSalaryWithBankView.as_view()),
    path('admin/payments/teacher/<int:payment_id>/retry-with-bank/', AdminRetryTeacherSalaryWithBankView.as_view()),
    
    # Bank transfer verification (works for both staff and teacher)
    path('admin/payments/verify/<str:payment_type>/<int:payment_id>/', AdminVerifyBankTransferView.as_view()),

    # Salary transfer OTP
    path('admin/payments/otp/send/', AdminSalaryTransferOtpSendView.as_view()),
    path('admin/payments/otp/resend/', AdminSalaryTransferOtpResendView.as_view()),
    path('admin/payments/otp/verify/', AdminSalaryTransferOtpVerifyView.as_view()),

    path('admin/staff-payments/mobile/', AdminStaffSalaryMobileListView.as_view(), name='admin-staff-payments-mobile'),
    path('admin/teacher-payments/mobile/', AdminTeacherSalaryMobileListView.as_view(), name='admin-teacher-payments-mobile'),
]
