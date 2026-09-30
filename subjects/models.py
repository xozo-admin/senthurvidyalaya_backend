from django.db import models
from academics.models import Standard

class Subject(models.Model):
    name = models.CharField(max_length=100) # e.g. "Mathematics"
    subject_code = models.CharField(max_length=20) # e.g. "MATH-10"
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='subjects')
    
    # Optional: If you want to link a 'Default Teacher' (Head of Subject), you can adding it later.

    class Meta:
        unique_together = ('name', 'standard')
        constraints = [
            models.UniqueConstraint(fields=['standard', 'subject_code'], name='unique_subject_code_per_standard')
        ]

    def __str__(self):
        return f"{self.name} ({self.standard.name})"
