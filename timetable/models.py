from django.db import models
from django.core.exceptions import ValidationError
from academics.models import Section,Standard
from teachers.models import Teacher
from subjects.models import Subject 
from school.models import AcademicYear  # <--- NEW IMPORT

class TimetableSlot(models.Model):
    DAYS_OF_WEEK = (
        ('Monday', 'Monday'), ('Tuesday', 'Tuesday'), ('Wednesday', 'Wednesday'),
        ('Thursday', 'Thursday'), ('Friday', 'Friday'), ('Saturday', 'Saturday'),
    )

    # 1. LINK TO ACADEMIC YEAR (Crucial for Year-Safe Scheduling)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='timetable_slots')

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='timetable_slots')
    day = models.CharField(max_length=15, choices=DAYS_OF_WEEK)
    
    period_no = models.IntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE) 
    
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True)
    # Optional grouping key for combined classes across sections.
    combined_group = models.CharField(max_length=36, null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['day', 'period_no']
        # 2. STRICT CONSTRAINTS (Prevent Double Booking)
        constraints = [
            # A: Section Clash - Class 10A cannot have 2 classes at Period 1 on Monday in 2025
            models.UniqueConstraint(
                fields=['academic_year', 'section', 'day', 'period_no'],
                name='unique_section_slot_per_year'
            ),
            # B: Teacher Clash - block overlaps when NOT a combined class slot.
            models.UniqueConstraint(
                fields=['academic_year', 'teacher', 'day', 'period_no'],
                condition=models.Q(teacher__isnull=False, combined_group__isnull=True),
                name='unique_teacher_slot_per_year_no_group'
            )
        ]

    def __str__(self):
        return f"{self.section} - {self.day} - {self.period_no} ({self.academic_year.name})"

# --- SUBSTITUTION (UNCHANGED) ---
class Substitution(models.Model):
    """
    Stores temporary overrides for a specific TimetableSlot on a specific Date.
    """
    slot = models.ForeignKey(TimetableSlot, on_delete=models.CASCADE, related_name='substitutions')
    date = models.DateField()
    
    # The new teacher taking the class
    substitute_teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    
    # The subject might change (e.g., Biology teacher absent -> Physics teacher takes over)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('date', 'slot')  # One substitution per slot per day

    def __str__(self):
        return f"Sub: {self.substitute_teacher.name} on {self.date} for {self.slot}"



# timetable/models.py

class ClassBreak(models.Model):
    # dynamic name (e.g., "Lunch", "Interval", "Prayer")
    name = models.CharField(max_length=50) 
    
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE)
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='breaks')
    
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ['start_time']
        # Ensure you don't have two "Lunch" breaks for the same class in the same year
        constraints = [
            models.UniqueConstraint(
                fields=['academic_year', 'standard', 'name'],
                name='unique_break_name_per_class'
            )
        ]

    def __str__(self):
        return f"{self.standard.name} - {self.name} ({self.start_time} - {self.end_time})"
