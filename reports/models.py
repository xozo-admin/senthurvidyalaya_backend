from django.db import models
from django.contrib.auth import get_user_model
from subjects.models import Subject
from exams.models import ExamTerm
from students.models import Enrollment # <--- FINAL IMPORT
from django.core.exceptions import ValidationError

User = get_user_model()

class BehaviorReport(models.Model):
    # 1. NEW STRICT FIELD (Replaces 'student')
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='behavior_reports')

    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posted_behavior')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name='behavior_reports')

    participation = models.FloatField(default=3.0)
    responsibility = models.FloatField(default=3.0)
    discipline = models.FloatField(default=3.0)
    attitude = models.FloatField(default=3.0)
    collaboration = models.FloatField(default=3.0)
    
    remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # NOW LINKED TO ENROLLMENT (Year-Safe)
        unique_together = ('enrollment', 'subject', 'term')

    @property
    def average_score(self):
        total = (self.participation + self.responsibility + self.discipline + 
                 self.attitude + self.collaboration)
        return round(total / 5, 1)


# --- ADD THESE TWO METHODS ---
    def clean(self):
        if self.term.academic_year != self.enrollment.academic_year:
            raise ValidationError("Term and Enrollment must belong to the same Academic Year.")

    def save(self, *args, **kwargs):
        self.clean() # Forces the check every single time a record is saved
        super().save(*args, **kwargs)