from django.db import models
from students.models import Student
from staff.models import NonTeachingStaff
from teachers.models import Teacher 
from school.models import AcademicYear

class LeaveRequest(models.Model):
    USER_TYPE_CHOICES = (
        ('student', 'Student'),
        ('staff', 'Non-Teaching Staff'),
        ('teacher', 'Teacher'),
    )
    
    STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    )

    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)
    
    # Linked Profiles
    student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True)
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE, null=True, blank=True)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, null=True, blank=True)

    # Academic Year (CRITICAL: Only for Students. Null for Staff/Teachers)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.SET_NULL, null=True, blank=True)

    # Details
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    proof_file = models.FileField(upload_to='leave_proofs/%Y/%m/', null=True, blank=True)
    
    # Approval Details
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='Pending')
    approved_by_name = models.CharField(max_length=100, blank=True, help_text="Name of Admin or Teacher who approved")
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        name = "Unknown"
        if self.student: name = self.student.name
        elif self.staff: name = self.staff.name
        elif self.teacher: name = self.teacher.name
        
        return f"{self.user_type}: {name} ({self.status})"