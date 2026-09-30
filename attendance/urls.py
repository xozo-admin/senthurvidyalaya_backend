from django.urls import path
from .views import (
    # --- EXISTING STUDENT VIEWS ---
    TeacherAttendanceView, 
    StudentAttendanceHistoryView,
    ClassDailySummaryView,
    UpdateAttendanceView,
    StudentWeeklyAttendanceImportView,
    TeacherAttendanceBulkImportView,
    TeacherViewClassAttendance,
    StudentMyAttendanceView,

    # --- NEW STAFF & TEACHER VIEWS ---
    AttendanceConfigView,
    MarkTeacherAttendanceView,
    MarkStaffAttendanceView,
    AdminDailyTeacherReportView,
    AdminDailyTeacherReportPaginatedView,
    AdminDailyStaffReportView,
    AdminDailyStaffReportPaginatedView,
    AdminTeacherHistoryView,
    AdminStaffHistoryView,
    TeacherAttendanceHistoryView,
    StaffAttendanceHistoryView,
    
    # --- NEW SIVA BRO api VIEWS ---
    AdminAttendanceOverviewView,
    SectionWiseAttendanceDetailView,
    TodayClassAttendanceReportView,
    ClassWiseAttendanceReportView,
    QRAttendanceSessionStartView,
    QRAttendanceSessionTokenView,
    QRAttendanceSessionCloseView,
    QRAttendanceScanMarkView
)

urlpatterns = [
    # ==========================================
    # 1. STUDENT ATTENDANCE (Existing)
    # ==========================================
    path('mark/', TeacherAttendanceView.as_view(), name='mark'),
    path('history/', StudentAttendanceHistoryView.as_view(), name='history'),
    path('daily-report/', ClassDailySummaryView.as_view(), name='summary'),
    path('update/', UpdateAttendanceView.as_view(), name='update'),
    path('import/student-weekly/', StudentWeeklyAttendanceImportView.as_view(), name='import-student-weekly'),
    path('import/teacher-bulk/', TeacherAttendanceBulkImportView.as_view(), name='import-teacher-bulk'),
    path('teacher-my-class/', TeacherViewClassAttendance.as_view(), name='teacher-my-class-attendance'),
    path('student/me/', StudentMyAttendanceView.as_view()),

    # ==========================================
    # 2. ADMIN CONFIGURATION
    # ==========================================
    path('config/', AttendanceConfigView.as_view(), name='attendance-config'),

    # ==========================================
    # 3. TEACHER ENDPOINTS (Self)
    # ==========================================
    path('teacher/mark/', MarkTeacherAttendanceView.as_view(), name='teacher-mark-attendance'),
    path('teacher/history/self/', TeacherAttendanceHistoryView.as_view(), name='teacher-own-history'),

    # ==========================================
    # 4. STAFF ENDPOINTS (Self)
    # ==========================================
    path('staff/mark/', MarkStaffAttendanceView.as_view(), name='staff-mark-attendance'),
    path('staff/history/self/', StaffAttendanceHistoryView.as_view(), name='staff-own-history'),

    # ==========================================
    # 5. ADMIN REPORTS (DAILY STATUS)
    # ==========================================
    path('admin/report/teacher/daily/', AdminDailyTeacherReportView.as_view(), name='admin-daily-teacher-report'),
    path('admin/report/teacher/daily/paginated/', AdminDailyTeacherReportPaginatedView.as_view(), name='admin-daily-teacher-report-paginated'),
    path('admin/report/staff/daily/', AdminDailyStaffReportView.as_view(), name='admin-daily-staff-report'),
    path('admin/report/staff/daily/paginated/', AdminDailyStaffReportPaginatedView.as_view(), name='admin-daily-staff-report-paginated'),

    # ==========================================
    # 6. ADMIN HISTORY (INDIVIDUAL)
    # ==========================================
    path('admin/history/teacher/', AdminTeacherHistoryView.as_view(), name='admin-teacher-history'),
    path('admin/history/staff/', AdminStaffHistoryView.as_view(), name='admin-staff-history'),



    # --- siva ---

    path('class-report/', ClassWiseAttendanceReportView.as_view(), name='class-wise-report'),
    path('today-class-report/', TodayClassAttendanceReportView.as_view(), name='today-class-wise-report'),
    path('class-detail/', SectionWiseAttendanceDetailView.as_view(), name='section-detail'),
    path('admin/overview/', AdminAttendanceOverviewView.as_view(), name='admin-attendance-overview'),

    # ==========================================
    # 7. DYNAMIC QR ATTENDANCE (TEACHER/STAFF)
    # ==========================================
    path('qr/session/start/', QRAttendanceSessionStartView.as_view(), name='qr-session-start'),
    path('qr/session/<int:session_id>/token/', QRAttendanceSessionTokenView.as_view(), name='qr-session-token'),
    path('qr/session/<int:session_id>/close/', QRAttendanceSessionCloseView.as_view(), name='qr-session-close'),
    path('qr/scan/', QRAttendanceScanMarkView.as_view(), name='qr-scan-mark'),


]
