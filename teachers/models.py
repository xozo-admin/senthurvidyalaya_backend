from django.db import models
from django.conf import settings 
from subjects.models import Subject 
from school.models import AcademicYear, School
from academics.models import Standard


TEACHER_PERMISSION_GROUPS = {
    "class": [
        "leave_approvals",
        "assignments",
        "performance",
        "exams",
        "students",
        "attendance",
    ],
    "actions": [
        "teacher_attendance",
        "attendance_logs",
        "todo",
        "announcements",
        "timetable",
        "reports",
        "salary",
        "transport",
        "meetings",
    ],
}

TEACHER_PERMISSION_LABELS = {
    "leave_approvals": "Leave Approvals",
    "assignments": "Assignments",
    "performance": "Performance",
    "exams": "Exams",
    "students": "Students",
    "attendance": "Class Attendance",
    "teacher_attendance": "Teacher Attendance",
    "attendance_logs": "View Logs",
    "todo": "To-Do List",
    "announcements": "Announcements",
    "timetable": "Timetable",
    "reports": "Reports",
    "salary": "Salary",
    "transport": "Transport",
    "meetings": "Meetings",
}


def default_teacher_role_permissions():
    return {key: True for key in TEACHER_PERMISSION_LABELS}


class Teacher(models.Model):
    # --- EXISTING FIELDS ---
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='teacher_profile',
        null=True
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='teachers',
        null=True,
        blank=True,
    )
    
    teacher_id = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    
    date_of_birth = models.DateField()
    joining_date = models.DateField(null=True, blank=True)
    qualification = models.CharField(max_length=150)
    department = models.CharField(max_length=100) 

    address = models.TextField(default="Not Provided")
    profile_image = models.ImageField(upload_to='profiles/teachers/', blank=True, null=True)
    extra_details = models.JSONField(default=dict, blank=True)
    bank_account_number = models.CharField(max_length=30, blank=True, null=True)
    ifsc_code = models.CharField(max_length=15, blank=True, null=True)
    account_holder_name = models.CharField(max_length=100, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    upi_id = models.CharField(max_length=50, blank=True, null=True)
    role_permissions = models.JSONField(default=default_teacher_role_permissions, blank=True)

    def __str__(self):
        return f"{self.name} ({self.department})"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school', 'teacher_id'], name='unique_teacher_id_per_school')
        ]

    def normalized_role_permissions(self):
        permissions = default_teacher_role_permissions()
        if isinstance(self.role_permissions, dict):
            for key in permissions:
                if key in self.role_permissions:
                    permissions[key] = bool(self.role_permissions[key])
        return permissions
    
    @property
    def current_class(self):
        # NEW LOGIC: Look for a ClassTeacher record for the Active Year
        # We filter by 'is_current=True' on the related AcademicYear
        assignment = self.class_teacher_of.filter(academic_year__is_current=True).first()
        
        if assignment: 
            return f"{assignment.section.standard.name} - {assignment.section.name}"
        return "No Class Assigned"


# --- UPDATED MODEL: TEACHER ALLOCATION ---
class TeacherAllocation(models.Model):
    """
    Layer 1 (The Setup):
    Defines WHO is allowed to teach WHAT to WHICH GRADE in a specific YEAR.
    "In 2025, Mr. Smith is approved to teach Physics to Standard 8."
    """
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='teacher_allocations')
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='teacher_allocations',
        null=True,
        blank=True,
    )
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='allocations')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    
    # CHANGED: Linked to Standard (Grade Level), not specific Section
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE)

    class Meta:
        # Prevent duplicate permissions (e.g., assigning Mr. Smith to Class 8 Physics twice in 2025)
        unique_together = ('academic_year', 'teacher', 'subject', 'standard')

    def __str__(self):
        return f"{self.teacher.name} -> {self.subject.name} (Class {self.standard.name}) [{self.academic_year.name}]"
