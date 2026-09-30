from django.urls import path
from .views import CreateToDoView, TeacherToDoListView, TeacherToDoDetailView, StudentTaskView, StudentTaskAllView, StudentTaskSummaryView

urlpatterns = [
    # Post a new task
    path('create/', CreateToDoView.as_view()),

    # View list of tasks for a class
    path('list/', TeacherToDoListView.as_view()),

    # View/Edit/Delete a specific task by task-number
    path('detail/', TeacherToDoDetailView.as_view()),

    # --- NEW STUDENT URL ---
    path('student/view/', StudentTaskView.as_view()),
    path('student/all/', StudentTaskAllView.as_view()),
    path('student/summary/', StudentTaskSummaryView.as_view()),
]
