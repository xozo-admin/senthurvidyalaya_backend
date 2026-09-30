from django.urls import path
from .views import (
    CreateTimetableView, 
    AdminTimetableView, 
    StudentTimetableView, 
    TeacherClassTimetableView,
    TeacherMyTimetableView,
    # --- NEW IMPORTS ---
    FreeTeachersForSubstitutionView,
    AssignSubstitutionView,
    RecentSubstitutionsView,
    StudentCurrentNextClassView,
    ClassBreakView,
    AdminTeacherTimetableView,
    AutoGenerateTimetableView
)

urlpatterns = [
    # 1. Admin: Create New Timetable
    path('create/', CreateTimetableView.as_view(), name='create-timetable'),

    # 2. Admin: Manage (View & Update) Timetable
    path('manage/', AdminTimetableView.as_view(), name='manage-timetable'),

    # 3. Student: View My Timetable
    path('student/my-timetable/', StudentTimetableView.as_view(), name='student-timetable'),
    
    # 4. Teacher: View My Class Timetable
    path('teacher/my-class-timetable/', TeacherClassTimetableView.as_view(), name='teacher-class-timetable'),

    # 5. Teacher: View THEIR OWN personal schedule
    path('my-schedule/', TeacherMyTimetableView.as_view(), name='teacher-personal-schedule'),

    # --- NEW: SUBSTITUTION ENDPOINTS ---
    # 6. Find free teachers for a specific date/period
    path('substitution/free-teachers/', FreeTeachersForSubstitutionView.as_view(), name='free-teachers'),

    # 7. Assign a substitute teacher
    path('substitution/assign/', AssignSubstitutionView.as_view(), name='assign-substitution'),

    # 8. Recent substitutions for class teacher
    path('substitution/recent/', RecentSubstitutionsView.as_view(), name='recent-substitutions'),

    path('student/dashboard/now/', StudentCurrentNextClassView.as_view()),

    path('break/', ClassBreakView.as_view(), name='manage-breaks'),

    # Admin viewing a specific Teacher's timetable
    path('admin/teacher-timetable/', AdminTeacherTimetableView.as_view(), name='admin-teacher-timetable'),

    path('auto-generate/', AutoGenerateTimetableView.as_view(), name='auto-generate-timetable'),
]
