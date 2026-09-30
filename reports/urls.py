from django.urls import path
from .views import (
    PostBehaviorView, 
    ClassTeacherReportDashboard, 
    StudentDetailedReportView,
    EditBehaviorView,
    TeacherViewBehavior,
    ClassBehaviorAnalysisView,  # <--- Import this
    StudentBehaviorDashboardView,  # <--- Import this

    ## SIVA BRO REPORTS URLS####
    GetSubmittedBehaviorReportsView,  # <--- Import this
)

urlpatterns = [
    path('post/', PostBehaviorView.as_view()),
    path('dashboard/', ClassTeacherReportDashboard.as_view()),
    path('details/', StudentDetailedReportView.as_view()),
    path('edit/', EditBehaviorView.as_view()),
    path('view-my-reports/', TeacherViewBehavior.as_view()),
    path('behavior-dashboard/', ClassBehaviorAnalysisView.as_view()), # Behavior Only (NEW)
    path('student/behavior-analytics/', StudentBehaviorDashboardView.as_view()),


### SIVA BRO REPORTS URLS # # ## #
    path('get-submitted-reports/', GetSubmittedBehaviorReportsView.as_view()),


]