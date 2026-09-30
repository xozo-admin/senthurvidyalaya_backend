from django.urls import path
from .views import SubjectMaterialView, SubjectMaterialFileView, StudentSubjectMaterialView, StudentMaterialsCombinedView

urlpatterns = [
    path('manage/', SubjectMaterialView.as_view()),
    path('file/', SubjectMaterialFileView.as_view()),

    # --- NEW STUDENT URL ---
    path('student/view/', StudentSubjectMaterialView.as_view()),
    path('student/combined/', StudentMaterialsCombinedView.as_view()),
]
