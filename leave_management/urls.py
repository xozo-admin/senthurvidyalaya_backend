from django.urls import path
from .views import (
    ApplyLeaveView, 
    AdminLeaveActionView, 
    AdminLeaveActionPaginatedView,
    TeacherApproveStudentLeaveView
)

urlpatterns = [
    # 1. Common: Apply, Edit, Delete, View History (Student, Staff, Teacher)
    path('apply/', ApplyLeaveView.as_view(), name='apply-leave'),

    # 2. Admin: Approve Staff & Teachers
    path('admin/action/', AdminLeaveActionView.as_view(), name='admin-leave-action'),
    path('admin/action/paginated/', AdminLeaveActionPaginatedView.as_view(), name='admin-leave-action-paginated'),

    # 3. Teacher: Approve Students (My Class Only)
    path('teacher/student-leaves/', TeacherApproveStudentLeaveView.as_view(), name='teacher-student-leaves'),
]
