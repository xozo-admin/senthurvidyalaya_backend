from django.db import models
from django.contrib.auth import get_user_model
from school.models import AcademicYear  # <--- NEW IMPORT

User = get_user_model()

class Assignment(models.Model):
    # 1. LINK TO ACADEMIC YEAR (Strict link)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='assignments')

    # Existing fields...
    class_name = models.CharField(max_length=10)
    section = models.CharField(max_length=5)
    subject = models.CharField(max_length=100)
    title = models.CharField(max_length=100)
    description = models.TextField()
    due_date = models.DateField()
    
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments_posted')
    attachment = models.FileField(upload_to='assignments/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # NEW FIELD
    updated_at = models.DateTimeField(auto_now=True)     # Updates EVERY time you save()

    def __str__(self):
        return f"{self.subject} - {self.title} ({self.class_name}-{self.section}) [{self.academic_year.name}]"

class AssignmentSubmission(models.Model):
    # No changes needed
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments_submitted')
    
    description = models.TextField(blank=True, null=True)
    file = models.FileField(upload_to='submissions/')
    submitted_at = models.DateTimeField(auto_now_add=True)
    
    marks = models.IntegerField(null=True, blank=True)
    graded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.student.username} -> {self.assignment.title}"