# class_resources/urls.py
from django.urls import path
from .views import ClassResourceView, ClassResourceFileView, ClassResourceSummaryView, ClassResourceAllView, StudentClassResourceView

urlpatterns = [
    # Manage Resource (Post, Get List, Edit Desc, Delete Whole)
    path('manage/', ClassResourceView.as_view()),
    path('all/', ClassResourceAllView.as_view()),
    path('summary/', ClassResourceSummaryView.as_view()),
    
    # Manage File Only (Delete File, Repost File)
    path('file/', ClassResourceFileView.as_view()),

    path('student/view/', StudentClassResourceView.as_view()),
]
