from django.urls import path
from .views import (
    ClassTeacherMarksView, ClassTeacherBehaviourView,
    SubjectTeacherMarksView, SubjectTeacherBehaviourView,

    # NEW VIEWS
    ClassTeacherExamMarksDetailView,
    ClassTeacherExamBehaviourDetailView,
    SubjectTeacherSpecificBehaviourTrendView,
    ClassTestAnalyticsView,
    StudentClassTestSummaryView,
    ClassTeacherDashboardView
)

urlpatterns = [
    # Class Teacher (Aggregate)
    path('class/marks/', ClassTeacherMarksView.as_view()),
    path('class/behaviour/', ClassTeacherBehaviourView.as_view()),

    # Subject Teacher (Specific)
    path('subject/marks/', SubjectTeacherMarksView.as_view()),
    path('subject/behaviour/', SubjectTeacherBehaviourView.as_view()),

    # NEW: Class Teacher Exam Specific Breakdowns
    path('class/exam/marks/', ClassTeacherExamMarksDetailView.as_view()),
    path('class/exam/behaviour/', ClassTeacherExamBehaviourDetailView.as_view()),

    # NEW: Subject Teacher Specific Category Trend
    path('subject/behaviour-type/', SubjectTeacherSpecificBehaviourTrendView.as_view()),

    path('analytics/class-test/', ClassTestAnalyticsView.as_view()),
    path('analytics/class-test/summary/', StudentClassTestSummaryView.as_view()),

    path('teacher/dashboard/overview/', ClassTeacherDashboardView.as_view(), name='teacher-dashboard-overview'),
]
