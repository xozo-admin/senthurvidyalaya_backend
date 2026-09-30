from django.db import models
from teachers.models import Teacher
from academics.models import Section
from subjects.models import Subject
from school.models import AcademicYear 
from django.utils import timezone

# ==========================================
# 1. TEACHER -> STUDENT ANNOUNCEMENTS
# ==========================================
class Announcement(models.Model):
    TYPE_CHOICES = (
        ('general', 'General'),
        ('academic', 'Academic'),
        ('class', 'Class'),
        ('subject', 'Subject'),
        ('exam', 'Exam'),
        ('event', 'Event'),
        ('emergency', 'Emergency'),
        ('parent', 'Parent'),
    )

    # 1. Link to Academic Year (The Partition)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='student_announcements')

    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, null=True, blank=True)
    
    # Auto-numbering for specific day (1, 2, 3...)
    announcement_number = models.IntegerField(default=1) 
    
    announcement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.teacher.name} - #{self.announcement_number} [{self.academic_year.name}]"

# ==========================================
# 2. ADMIN -> STAFF ANNOUNCEMENTS
# ==========================================
class StaffAnnouncement(models.Model):
    VISIBILITY_CHOICES = [
        ('ALL_STAFF', 'All Staff Only'),
        ('ROLE_SPECIFIC', 'Specific Role Only'),
    ]

    # 1. Link to Academic Year (Archiving)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='staff_announcements')

    title = models.CharField(max_length=255)
    description = models.TextField() 
    date = models.DateField(default=timezone.localdate)
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='ALL_STAFF')
    target_role = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Staff: {self.title} [{self.academic_year.name}]"

# ==========================================
# 3. ADMIN -> EVERYONE ANNOUNCEMENTS
# ==========================================
class CommonAnnouncement(models.Model):
    # 1. Link to Academic Year (Events like Sports Day 2025)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='common_announcements')

    title = models.CharField(max_length=255)
    description = models.TextField() 
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Common: {self.title} [{self.academic_year.name}]"


# ==========================================
# 4. ADMIN -> TEACHER ANNOUNCEMENTS (NEW)
# ==========================================

TEACHER_VISIBILITY_CHOICES = [
    ('ALL_TEACHERS', 'All Teachers'),
    ('ROLE_SPECIFIC', 'Role Specific'),
]

TEACHER_ROLE_CHOICES = [
    ('CLASS_TEACHER', 'Class Teacher'),
    ('SUBJECT_TEACHER', 'Subject Teacher'),
]

class TeacherAnnouncement(models.Model):
    # Link to Academic Year
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='teacher_announcements')

    title = models.CharField(max_length=255)
    description = models.TextField()
    date = models.DateField(auto_now_add=True)
    
    visibility = models.CharField(max_length=20, choices=TEACHER_VISIBILITY_CHOICES, default='ALL_TEACHERS')
    
    # If ROLE_SPECIFIC, this field tells us WHICH role (Class vs Subject)
    target_role = models.CharField(max_length=20, choices=TEACHER_ROLE_CHOICES, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Teacher Ann: {self.title} ({self.visibility})"