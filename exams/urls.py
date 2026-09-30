from django.urls import path
from .views import (
    UploadMarksView, 
    ClassExamResultView, 
    StudentExamDetailView, 
    EditStudentMarkView, 
    SubjectExamAnalysisView, 
    ClassTeacherStudentMarksView, 
    StudentPerformanceDashboardView, 
    AdminExamScheduleView, 
    StudentExamDashboardView, 
    StudentExamTimetableView,
    AdminExamTermView,
    ExamDropdownView,
    TeacherScheduledExamsView,
    AdminMarkApprovalView,
    SubjectMarksListView,  # SIVA BRO
    TeacherTermDropdownView,
    StudentAvailableExamsView,
    TeacherAvailableExamsView,
    StudentLatestExamComparisonView,
    AdminCalendarWidgetView,
    AdminExamOverviewView,
    AdminExamTermScheduleView
)


urlpatterns = [
    # --- ADMIN: SETUP ---
    # 1. Create/View Exam Types (e.g., "Unit Test 1 - 25 Marks")
    path('admin/terms/', AdminExamTermView.as_view(), name='admin-exam-terms'),
    
    # 2. Create/View Exam Schedules (Dates & Timetables)
    path('admin/schedule/', AdminExamScheduleView.as_view(), name='admin-exam-schedule'),
    path('admin/term-schedules/', AdminExamTermScheduleView.as_view(), name='admin-term-schedules'),


    # --- TEACHER: MARKS ENTRY & ANALYSIS ---
    # 3. Upload Marks
    path('upload/', UploadMarksView.as_view(), name='upload-marks'),

    # 4. Edit Marks (PUT method)
    path('edit-marks/', EditStudentMarkView.as_view(), name='edit-marks'),

    # 5. Class Results (Summary View)
    path('class-result/', ClassExamResultView.as_view(), name='class-result'),

    # 6. Subject Analysis (Pass/Fail Stats)
    path('subject-analysis/', SubjectExamAnalysisView.as_view(), name='subject-analysis'),

    # 7. Student Detail (Specific Student's Marks)
    path('student-result/', StudentExamDetailView.as_view(), name='student-detail'),
    
    # 8. Class Teacher View (Detailed Student Marks)
    path('student-marks-detail/', ClassTeacherStudentMarksView.as_view(), name='teacher-student-detail'),


    # --- STUDENT: DASHBOARD ---
    # 9. Exam Overview (Upcoming/Ongoing/Completed)
    path('student/dashboard/', StudentExamDashboardView.as_view(), name='student-exam-dashboard'), 

    # 10. Detailed Timetable
    path('student/timetable/', StudentExamTimetableView.as_view(), name='student-timetable'), 

    # 11. Performance Graphs (The Dynamic Dashboard)
    path('student/analytics/', StudentPerformanceDashboardView.as_view(), name='student-analytics'),

    path('list/', ExamDropdownView.as_view()),              # DROPDOWNS

    # exams/urls.py
path('teacher/schedule/', TeacherScheduledExamsView.as_view(), name='teacher-exam-schedule'),

  # 2. ADMIN: Approve Requests (ADD THIS ONE ONLY)
    path('admin/approvals/', AdminMarkApprovalView.as_view(), name='admin-approvals'),

    path('terms/dropdown/', TeacherTermDropdownView.as_view(), name='teacher-term-dropdown'),

    # ... your other urls
    path('student/available-exams/', StudentAvailableExamsView.as_view(), name='student-available-exams'),

    # ... other urls ...
    path('teacher/available-exams/', TeacherAvailableExamsView.as_view(), name='teacher-exam-list'),

    # student dashboard for exam subjects last vs latese
    path('student/dashboard/latest-comparison/', StudentLatestExamComparisonView.as_view(), name='student-latest-comparison'),


    # ----- siva ----

    path('list-marks/', SubjectMarksListView.as_view(), name='subject-marks-list'),

path('admin/calendar/', AdminCalendarWidgetView.as_view(), name='admin-calendar-widget'),

path('admin/overview/', AdminExamOverviewView.as_view(), name='admin-exam-overview'),


]
