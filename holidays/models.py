from django.db import models

class Holiday(models.Model):
    APPLICABLE_CHOICES = (
        ('everyone', 'Everyone (School Closed)'),
        ('students_only', 'Students Only'),
        ('staff', 'Non-Teaching Staff Only'),  # Changed key to 'staff' to match views
        ('teachers', 'Teachers Only'),         # <--- NEW ADDITION
    )

    date = models.DateField(unique=True)
    name = models.CharField(max_length=100) 
    applicable_for = models.CharField(max_length=20, choices=APPLICABLE_CHOICES, default='everyone')

    def __str__(self):
        return f"{self.name} - {self.date} ({self.applicable_for})"