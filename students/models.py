from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError  # Important for validation
# Import both Section AND Standard
from academics.models import Section, Standard 
from school.models import AcademicYear, School

STUDENT_PERMISSION_GROUPS = {
    "home": ["transport"],
    "tasks": ["timetable", "assignments", "todo", "attendance", "fee"],
    "exams": ["exam_schedule", "performance"],
    "meetings": ["meetings"],
    "updates": ["updates"],
}

STUDENT_PERMISSION_LABELS = {
    "transport": "Transport",
    "timetable": "Timetable",
    "assignments": "Assignments",
    "todo": "To-Do List",
    "attendance": "Attendance",
    "fee": "Fee",
    "exam_schedule": "Exam Schedule",
    "performance": "Performance",
    "meetings": "Meetings",
    "updates": "Updates",
}


def default_student_role_permissions():
    return {key: True for key in STUDENT_PERMISSION_LABELS}


class StudentRolePermission(models.Model):
    school = models.OneToOneField(
        School,
        on_delete=models.CASCADE,
        related_name='student_role_permission',
    )
    role_permissions = models.JSONField(default=default_student_role_permissions, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def normalized_role_permissions(self):
        permissions = default_student_role_permissions()
        if isinstance(self.role_permissions, dict):
            for key in permissions:
                if key in self.role_permissions:
                    permissions[key] = bool(self.role_permissions[key])
        return permissions

    def __str__(self):
        return f"Student roles - {self.school.name}"


class Student(models.Model):
    ACCOMMODATION_CHOICES = (
        ('day_scholar', 'Day Scholar'),
        ('hosteller', 'Hosteller'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='student_profile',
        null=True
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='students',
        null=True,
        blank=True,
    )
    student_id = models.CharField(max_length=50)
    student_name = models.CharField(max_length=150)
    student_email = models.EmailField(null=True, blank=True)
    
    father_name = models.CharField(max_length=150)
    mother_name = models.CharField(max_length=150)
    father_phone = models.CharField(max_length=20)
    mother_phone = models.CharField(max_length=20)
    
    date_of_birth = models.DateField()
    profile_image = models.ImageField(upload_to='profiles/students/', blank=True, null=True)
    gender = models.CharField(max_length=20)
    accommodation = models.CharField(max_length=20, choices=ACCOMMODATION_CHOICES, null=True, blank=True)

    # --- NEW FIELD: CLASS (STANDARD) ---
    # We allow null=True temporarily so existing students don't crash the database.
    # We will fill this data in the next step.
    standard = models.ForeignKey(
        Standard,
        on_delete=models.PROTECT,
        related_name='students',
        null=True,  
        blank=True
    )

    # --- SECTION ---
    section = models.ForeignKey(
        Section, 
        on_delete=models.PROTECT,
        related_name='students',
        null=True, 
        blank=True
    )

    date_of_admission = models.DateField(default=timezone.now)
    address = models.TextField(default="Not Provided") 
    extra_details = models.JSONField(default=dict, blank=True) 
    role_permissions = models.JSONField(default=default_student_role_permissions, blank=True)

    def normalized_role_permissions(self):
        if self.school_id:
            try:
                return self.school.student_role_permission.normalized_role_permissions()
            except StudentRolePermission.DoesNotExist:
                pass

        permissions = default_student_role_permissions()
        if isinstance(self.role_permissions, dict):
            for key in permissions:
                if key in self.role_permissions:
                    permissions[key] = bool(self.role_permissions[key])
        return permissions

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school', 'student_id'], name='unique_student_id_per_school')
        ]

    def clean(self):
        """
        Validation Logic:
        Ensure the selected Section actually belongs to the selected Standard.
        """
        if self.section and self.standard:
            if self.section.standard != self.standard:
                raise ValidationError(
                    f"Conflict: Section '{self.section.name}' belongs to "
                    f"'{self.section.standard.name}', but you selected '{self.standard.name}'."
                )

    def save(self, *args, **kwargs):
        self.clean()  # Run validation before saving
        super().save(*args, **kwargs)

    def __str__(self):
        if self.standard:
             # Standard is known
             class_info = self.standard.name
             if self.section:
                 class_info += f"-{self.section.name}"
             return f"{self.student_name} ({class_info})"
        return f"{self.student_name} (No Class Assigned)"


class Enrollment(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='enrollments',
        null=True,
        blank=True,
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='enrollments')
    
    # The Class/Grade
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='enrollments')
    
    # The Section (e.g., 'A', 'B')
    section = models.ForeignKey(Section, on_delete=models.CASCADE, null=True, blank=True, related_name='enrollments')
    
    # The Year (The Time Machine)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='enrollments')
    
    # Status
    is_active = models.BooleanField(default=True)
    promoted = models.BooleanField(default=False, help_text="True if student has been promoted from this class")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # A student can only be in ONE standard/section per Academic Year
        unique_together = ['student', 'academic_year'] 
        ordering = ['standard', 'section', 'student__student_name']

    def __str__(self):
        sec_name = f"-{self.section.name}" if self.section else ""
        return f"{self.student.student_name} - {self.standard.name}{sec_name} ({self.academic_year.name})"
