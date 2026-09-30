from django.urls import path
from .views import (
    AcademicYearDetailView,
    AcademicYearListCreateView,
    InstitutionDetailView,
    InstitutionListCreateView,
    SchoolDetailView,
    SchoolListCreateView,
)

urlpatterns = [
    path('institutions/', InstitutionListCreateView.as_view(), name='institution-list'),
    path('institutions/<int:pk>/', InstitutionDetailView.as_view(), name='institution-detail'),
    path('schools/', SchoolListCreateView.as_view(), name='school-list'),
    path('schools/<int:pk>/', SchoolDetailView.as_view(), name='school-detail'),
    # API to Get all or Create new: /api/school/academic-years/
    path('academic-years/', AcademicYearListCreateView.as_view(), name='academic-year-list'),
    
    # API to Get one, Edit, or Delete: /api/school/academic-years/5/
    path('academic-years/<int:pk>/', AcademicYearDetailView.as_view(), name='academic-year-detail'),
]
