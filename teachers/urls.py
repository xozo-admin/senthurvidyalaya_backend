# teachers/urls.py
from django.urls import path
from .views import (
    TeacherProfileView,          # Teacher viewing themselves
    AdminTeacherListView,        # Admin viewing all teachers
    TeacherDetailView,           # Admin viewing/editing a specific teacher
    TeacherStudentDetailView,    # Teacher viewing a specific student
    AdminTeacherDropdownView,    # Simple list for dropdowns
    AssignTeacherSubjectView,     # <--- The NEW view for assigning subjects & classes
    ClassTestManagerView,        # Create/List/Delete Tests
    ClassTestMarksView,          # Enter/View Marks
    TeacherHandledSubjectsView,  # Teacher viewing their handled subjects
    TeacherRolePermissionView,
    

    ### SIVA BRO TEACHER URLS
    TeachersByClassView,
    AllTeachersAllocationsView,
    TeacherClassSubjectsView,
    TeacherSubjectAllocationsView

)

urlpatterns = [
    # 1. Teacher viewing their own profile (Home Screen)
    path('profile/', TeacherProfileView.as_view(), name='teacher-profile-me'),

    # 2. Admin viewing ALL teachers (Detailed list)
    path('list/all/', AdminTeacherListView.as_view(), name='admin-teacher-list'),

    # 3. Admin viewing a SINGLE teacher by ID (e.g., ?teacher_id=TCH-001)
    path('details/', TeacherDetailView.as_view(), name='admin-teacher-detail'),

    # 4. Teacher viewing a specific student in their class
    path('my-class/student/detail/', TeacherStudentDetailView.as_view(), name='teacher-student-detail'),

    # 5. Admin Assigning Subjects & Classes (THIS IS THE ONE YOU NEED NOW)
    path('assign-subject/', AssignTeacherSubjectView.as_view(), name='assign-teacher-subject'),
    
    # 6. Simple list for Dropdowns (ID and Name only)
    path('list/simple/', AdminTeacherDropdownView.as_view(), name='teacher-list-simple'),

    path('tests/manage/', ClassTestManagerView.as_view()), # Create/List/Delete Tests
    path('tests/marks/', ClassTestMarksView.as_view()),    # Enter/View Marks

    # 7. Teacher viewing their handled subjects
    path('my-handled-subjects/', TeacherHandledSubjectsView.as_view(), name='teacher-handled-subjects'),
    path('role-permissions/', TeacherRolePermissionView.as_view(), name='teacher-role-permissions'),

##### SIVA BRO TEACHER URLS ####
    path('teachers-by-class/', TeachersByClassView.as_view(), name='teachers-by-class'),
    path('all-allocations/', AllTeachersAllocationsView.as_view(), name='all-teachers-allocations'),
    path('my-class-subjects/', TeacherClassSubjectsView.as_view(), name='teacher-class-subjects'),
    path('subject-allocations/', TeacherSubjectAllocationsView.as_view(), name='teacher-subject-allocations'),]
