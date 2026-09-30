from django.db import models
from teachers.models import Teacher
from academics.models import Section
from subjects.models import Subject
from school.models import AcademicYear  # <--- NEW IMPORT

class ToDoTask(models.Model):
    PRIORITY_CHOICES = (
        ('important', 'Important'),
        ('normal', 'Normal'),
        ('urgent', 'Urgent'),
    )

    TASK_TYPE_CHOICES = (
        ('reading', 'Reading'),
        ('writing', 'Writing'),
        ('assignment', 'Assignment'),
        ('project', 'Project'),
    )

    # 1. LINK TO ACADEMIC YEAR (Crucial for filtering history)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='todo_tasks')

    # Who is posting?
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    
    # Where is it posted?
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    
    # When?
    date = models.DateField()
    
    # Identification
    task_number = models.IntegerField()  # Auto-calculated (1, 2, 3 per day)

    # Content
    title = models.CharField(max_length=255)
    task_type = models.CharField(max_length=50, choices=TASK_TYPE_CHOICES)
    estimated_time = models.IntegerField(help_text="Time in minutes")
    teacher_note = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Ensure a teacher doesn't have duplicate task numbers on the same day
        unique_together = ('teacher', 'date', 'task_number')
        ordering = ['-date', 'task_number']

    def __str__(self):
        return f"{self.date} | Task {self.task_number} | {self.teacher.name} [{self.academic_year.name}]"