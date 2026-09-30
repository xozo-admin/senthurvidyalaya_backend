from django.db import models
from django.conf import settings
from academics.models import Section
from subjects.models import Subject
from school.models import AcademicYear  # <--- NEW IMPORT

class ClassResource(models.Model):
    # 1. Link to Academic Year (Partitioning resources by session)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='class_resources')

    # Auto-linked to the Class Teacher's assigned Section
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='class_resources')
    
    # The teacher who posted it
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Optional: If Null -> "Overall Resource". If Set -> "Subject Resource".
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, null=True, blank=True, related_name='class_resources')

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True) # Description is optional
    
    # The File (PDF, Doc, Image)
    file = models.FileField(upload_to='class_resources/', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.section}) [{self.academic_year.name}]"