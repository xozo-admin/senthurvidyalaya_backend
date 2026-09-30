from django.db import models
from school.models import School

class Standard(models.Model):
    """
    Represents the Grade Level (e.g., 10, 11, 12, LKG)
    """
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='standards',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=10) # e.g. "10"
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school', 'name'], name='unique_standard_per_school')
        ]

    def __str__(self):
        return f"Class {self.name}"

class Section(models.Model):
    """
    Represents the division (e.g., A, B) inside a Class.
    """
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='sections',
        null=True,
        blank=True,
    )
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=5) # e.g. "A"
    
    # --- DELETED: class_teacher field is gone. We use the model below instead. ---

    class Meta:
        # Prevents creating "10-A" twice
        unique_together = ['standard', 'name']
        ordering = ['standard', 'name']

    def __str__(self):
        return f"{self.standard.name} - {self.name}"


# --- NEW MODEL: YEAR-SAFE CLASS TEACHER ---
class ClassTeacher(models.Model):
    """
    Links a Teacher to a Section for a specific Academic Year.
    """
    # Use string references to avoid circular imports
    academic_year = models.ForeignKey('school.AcademicYear', on_delete=models.CASCADE)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='class_teachers',
        null=True,
        blank=True,
    )
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    
    teacher = models.ForeignKey(
        'teachers.Teacher', 
        on_delete=models.CASCADE,
        related_name='class_teacher_of' # Allows teacher.class_teacher_of.all()
    )

    class Meta:
        unique_together = [
            # 1. A Section can only have ONE class teacher per year
            ('academic_year', 'section'),
            # 2. A Teacher can usually only be class teacher for ONE section per year
            ('academic_year', 'teacher'), 
        ]

    def __str__(self):
        return f"{self.teacher.name} - {self.section} ({self.academic_year.name})"
