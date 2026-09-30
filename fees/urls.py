# fees/urls.py

from django.urls import path
from .views import (
    AssignFeeView, 
    InitiatePaymentView,
    VerifyPaymentView,
    CancelPaymentView,
    RecordOfflinePaymentView,
    StudentFeeSummaryView,
    ReceiptView,
    ViewFeeStructureView, 
    UpdateFeeStructureView, 
    ApplyConcessionView, 
    DailyCollectionReportView,
    ClassFeeReportView,
    SchoolDueReportView,
    DeleteFeeView,
    FeeTypeListView,
    RazorpayWebhookView,
    AdminFeeStatsCardsView,
    FeeOverviewChartView,
    SendFeeRemindersView,
)

urlpatterns = [
    # 1. Admin: Assign & Manage Fees
    path('assign/', AssignFeeView.as_view(), name='assign-fee'),
    path('structure/view/', ViewFeeStructureView.as_view(), name='view-structure'),
    path('structure/update/', UpdateFeeStructureView.as_view(), name='update-structure'),
    path('concession/', ApplyConcessionView.as_view(), name='apply-concession'),
    path('delete/', DeleteFeeView.as_view(), name='delete-fee'),
    path('types/', FeeTypeListView.as_view(), name='fee-type-list'),

    # 2. Student: Payment & Receipt
    path('payment/initiate/', InitiatePaymentView.as_view(), name='initiate-payment'),
    path('payment/verify/', VerifyPaymentView.as_view(), name='verify-payment'),
    path('payment/cancel/', CancelPaymentView.as_view(), name='cancel-payment'),
    path('payment/offline/', RecordOfflinePaymentView.as_view(), name='record-offline-payment'),
    path('receipt/', ReceiptView.as_view(), name='get-receipt'),
    path('student/summary/', StudentFeeSummaryView.as_view(), name='student-fee-summary'),

    # 3. Admin: Reports
    path('report/class/', ClassFeeReportView.as_view(), name='class-fee-report'),
    path('report/due-school/', SchoolDueReportView.as_view(), name='school-due-report'),
    path('report/daily/', DailyCollectionReportView.as_view(), name='daily-report'),
    path('report/stats-cards/', AdminFeeStatsCardsView.as_view(), name='fee-stats-cards'),
    path('report/overview-chart/', FeeOverviewChartView.as_view(), name='fee-overview-chart'),
    path('reminders/send/', SendFeeRemindersView.as_view(), name='send-fee-reminders'),

    # 4. Webhook (No authentication)
    path('webhook/razorpay/', RazorpayWebhookView.as_view(), name='razorpay-webhook'),
]
