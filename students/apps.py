# students/apps.py
from django.apps import AppConfig

class StudentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'students'

    #def ready(self):
        # This triggers the automatic mapping for students
        #import students.signals