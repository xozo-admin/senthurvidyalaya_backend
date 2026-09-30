from django.db import models
from students.models import Enrollment  # <--- CHANGED: Import Enrollment, not Student
from staff.models import NonTeachingStaff
from django.utils import timezone
from teachers.models import Teacher
from django.conf import settings


# ==========================================
#  1. STUDENT ATTENDANCE (Updated)
# ==========================================
class Attendance(models.Model):
    STATUS_CHOICES = [
        ('Present', 'Present'),
        ('Absent', 'Absent'),
        ('Late', 'Late'),
    ]

    # NOW we make it required (null=False is default)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='attendance_records')
    
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='Present')
    remarks = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        unique_together = ['enrollment', 'date']
        ordering = ['-date', 'enrollment__student__student_name']

    def __str__(self):
        return f"{self.enrollment.student.student_name} - {self.date} - {self.status}"

# ==========================================
#  2. STAFF ATTENDANCE (No Changes Needed)
# ==========================================
class StaffAttendance(models.Model):
    # Staff are permanent employees, so they don't depend on "Academic Years"
    # Keeping this exactly as you had it is perfectly fine.
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(default=timezone.now)
    check_in_time = models.TimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='Present') 

    class Meta:
        unique_together = ('staff', 'date') 
        verbose_name_plural = "Staff Attendance"

    def __str__(self):
        return f"{self.staff.name} - {self.date} ({self.check_in_time})"


# 3. TEACHER (New)
class TeacherAttendance(models.Model):
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(default=timezone.now)
    check_in_time = models.TimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='Present') 

    class Meta:
        unique_together = ('teacher', 'date') 

# 4. CONFIG (New)
class AttendanceConfig(models.Model):
    school_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    school_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    allowed_radius_meters = models.IntegerField(default=200)
    late_cutoff_time = models.TimeField(default="09:00:00") 

    def __str__(self): return "Attendance Settings"


class QRAttendanceSession(models.Model):
    ROLE_SCOPE_CHOICES = [
        ('teacher', 'Teacher'),
        ('staff', 'Staff'),
        ('both', 'Both'),
    ]

    role_scope = models.CharField(max_length=10, choices=ROLE_SCOPE_CHOICES, default='both')
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    rotation_seconds = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='qr_attendance_sessions'
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"QR Session #{self.id} ({self.role_scope})"


class QRAttendanceScanLog(models.Model):
    session = models.ForeignKey(QRAttendanceSession, on_delete=models.CASCADE, related_name='scan_logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='qr_attendance_scan_logs')
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, related_name='qr_scan_logs')
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.SET_NULL, null=True, blank=True, related_name='qr_scan_logs')
    token_slot = models.PositiveIntegerField()
    accepted = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)
    marked_status = models.CharField(max_length=20, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-scanned_at']
        indexes = [
            models.Index(fields=['session', 'accepted']),
            models.Index(fields=['teacher', 'session']),
            models.Index(fields=['staff', 'session']),
        ]

    def __str__(self):
        who = self.teacher.teacher_id if self.teacher else (self.staff.staff_id if self.staff else self.user_id)
        return f"QR Scan - Session {self.session_id} - {who} - {'OK' if self.accepted else 'Rejected'}"
