from django.urls import path
from django.urls import path, include  # <--- 1. Make sure 'include' is imported
from rest_framework.routers import DefaultRouter  # <--- 2. Import DefaultRouter

from .views import (
    StudentProfileView,         # Existing: Student viewing themselves
    StudentDetailView,          # New: Admin/Teacher viewing specific student
    StudentListView,            # New: Class Roster
    AdminAssignStudentsView,     # New: Admin Bulk Assign
    SubjectTeacherStudentListView,
    EnrollmentViewSet,
    StudentOverviewView,
    SubjectTeacherStudentProfileView,
    StudentRolePermissionView,
)
# 4. Create the Router and Register the ViewSet
router = DefaultRouter()
router.register(r'enrollments', EnrollmentViewSet, basename='enrollment')

urlpatterns = [
    # 1. Student viewing their own profile (Home Screen)
    # URL: /api/students/profile/
    path('profile/', StudentProfileView.as_view(), name='student-profile-me'),

    path('role-permissions/', StudentRolePermissionView.as_view(), name='student-role-permissions'),

    # 2. Admin/Teacher viewing a specific student by ID (e.g., ?student_id=1001)
    # URL: /api/students/details/?student_id=1001
    path('details/', StudentDetailView.as_view(), name='student-details'),

    # 3. List of students (Teacher gets their class, Admin searches by class/sec)
    # URL: /api/students/list/
    path('list/', StudentListView.as_view(), name='student-list'),

    # 4. Admin assigning multiple students to a class
    # URL: /api/students/assign-bulk/
    path('assign-bulk/', AdminAssignStudentsView.as_view(), name='student-assign-bulk'),

    path('subject-teacher-list/', SubjectTeacherStudentListView.as_view()),

     # ... your other student urls ...
    path('subject-teacher/student/profile/', SubjectTeacherStudentProfileView.as_view(), name='subject-teacher-student-profile'),

    # --- 5. Add the Router URLs at the bottom ---
    # This enables: /api/students/enrollments/ (GET, POST)
    # And: /api/students/enrollments/5/ (PUT, DELETE)
    path('', include(router.urls)),

    path('overview/', StudentOverviewView.as_view(), name='student-overview'),
]
