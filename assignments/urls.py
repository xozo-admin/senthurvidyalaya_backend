from django.urls import path
from .views import (
    TeacherAssignmentManagerView,
    TeacherFileOperationsView,
    StudentAssignmentListView, 
    StudentAssignmentCombinedView,
    SubmitAssignmentView, 
    GradeSubmissionView, 
    MonthlyReportView,     
    StudentAssignmentFeedView,
    StudentSubmissionManagerView,
    TeacherSubmissionListView

)

urlpatterns = [
    # --- TEACHER OPERATIONS (NEW) ---
    # Handles: Create (POST), View Mine (GET), Edit Desc (PUT), Delete All (DELETE)
    path('teacher/manage/', TeacherAssignmentManagerView.as_view()),
    
    # Handles: Delete PDF Only (DELETE), Repost File (POST)
    path('teacher/file/', TeacherFileOperationsView.as_view()),

    # --- STUDENT & GRADING (EXISTING) ---
    path('my-assignments/', StudentAssignmentListView.as_view()),
    path('student/combined/', StudentAssignmentCombinedView.as_view()),
    path('submit/', SubmitAssignmentView.as_view()),
    path('grade/', GradeSubmissionView.as_view()),
    path('report/', MonthlyReportView.as_view()),

    # --- NEW STUDENT URLs ---
    # 1. View Teacher's Posts
    path('student/feed/', StudentAssignmentFeedView.as_view()),    
    # 2. Manage My Submissions (Post, View My Work, Edit, Delete)
    path('student/submit/', StudentSubmissionManagerView.as_view()),

    # 3. Teacher: View Class Tracker for Assignment
    path('teacher/submissions/', TeacherSubmissionListView.as_view(), name='teacher-assignment-submissions'),
]
