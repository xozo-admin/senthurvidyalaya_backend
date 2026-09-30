from django.db import models
from django.conf import settings
from subjects.models import Subject
from school.models import AcademicYear  # <--- NEW IMPORT

class SubjectMaterial(models.Model):
    # 1. Link to Academic Year (Partitioning materials by session)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='subject_materials')

    # The Teacher who posted it
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Target Audience (Stored as text since Subject Teachers might not own the Section object)
    class_name = models.CharField(max_length=10) # e.g. "10"
    section = models.CharField(max_length=10)    # e.g. "A"
    
    # Strictly link to a Subject object
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='materials')

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    
    # The File (PDF, Doc, Image)
    file = models.FileField(upload_to='subject_materials/', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.subject.name}: {self.title} ({self.class_name}-{self.section}) [{self.academic_year.name}]"