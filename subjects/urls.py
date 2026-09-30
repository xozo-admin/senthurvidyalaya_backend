from django.urls import path
from .views import BulkAssignSubjectsView, ViewClassSubjects, StudentAllocatedSubjectsView, ViewAllClassSubjects ##SIVA BRO 

urlpatterns = [
    path('assign/bulk/', BulkAssignSubjectsView.as_view(), name='assign-subjects'),

    path('view/', ViewClassSubjects.as_view(), name='view-class-subjects'),

    # ---- siva ----

   path('view/all/', ViewAllClassSubjects.as_view(), name='view-all-class-subjects'),
    #### SIVA BRO SUBJECT URSL # # # #
    path('student/my-subjects/', StudentAllocatedSubjectsView.as_view(), name='student-subjects'),

]