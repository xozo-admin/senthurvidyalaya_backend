from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from school.models import AcademicYear
from staff.models import NonTeachingStaff
from students.models import Student


class HostelBlock(models.Model):
    GENDER_CHOICES = (
        ('boys', 'Boys'),
        ('girls', 'Girls'),
        ('coed', 'Co-Ed'),
    )

    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    gender_policy = models.CharField(max_length=10, choices=GENDER_CHOICES, default='coed')
    is_active = models.BooleanField(default=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')

    def __str__(self):
        return self.name


class HostelRoom(models.Model):
    ROOM_TYPE_CHOICES = (
        ('standard', 'Standard'),
        ('ac', 'AC'),
        ('deluxe', 'Deluxe'),
    )

    block = models.ForeignKey(HostelBlock, on_delete=models.CASCADE, related_name='rooms')
    room_number = models.CharField(max_length=30)
    floor = models.IntegerField(default=0)
    room_type = models.CharField(max_length=20, choices=ROOM_TYPE_CHOICES, default='standard')
    capacity = models.PositiveIntegerField(default=1)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        unique_together = ('block', 'room_number')
        ordering = ['block__name', 'floor', 'room_number']

    def __str__(self):
        return f"{self.block.name} - {self.room_number}"


class HostelBed(models.Model):
    room = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name='beds')
    bed_number = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        unique_together = ('room', 'bed_number')
        ordering = ['room__block__name', 'room__room_number', 'bed_number']

    def __str__(self):
        return f"{self.room} / Bed {self.bed_number}"


class HostelWardenAssignment(models.Model):
    block = models.ForeignKey(HostelBlock, on_delete=models.CASCADE, related_name='warden_assignments')
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE, related_name='hostel_warden_blocks')
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['block', 'staff'],
                condition=Q(is_active=True),
                name='uniq_active_warden_per_block_staff',
            )
        ]

    def __str__(self):
        return f"{self.staff.name} -> {self.block.name}"


class HostelAllocation(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='hostel_allocations')
    bed = models.ForeignKey(HostelBed, on_delete=models.PROTECT, related_name='allocations')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='hostel_allocations')

    check_in_date = models.DateField()
    expected_check_out_date = models.DateField(null=True, blank=True)
    check_out_date = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['student'],
                condition=Q(is_active=True),
                name='uniq_active_hostel_allocation_per_student',
            ),
            models.UniqueConstraint(
                fields=['bed'],
                condition=Q(is_active=True),
                name='uniq_active_hostel_allocation_per_bed',
            ),
        ]
        ordering = ['-is_active', '-created_at']

    def clean(self):
        if self.is_active and not self.bed.is_active:
            raise ValidationError('Cannot allocate an inactive bed.')

        if self.check_out_date and self.check_out_date < self.check_in_date:
            raise ValidationError('Check-out date cannot be before check-in date.')

        if self.expected_check_out_date and self.expected_check_out_date < self.check_in_date:
            raise ValidationError('Expected check-out date cannot be before check-in date.')

        if self.is_active and self.bed.room and not self.bed.room.is_active:
            raise ValidationError('Cannot allocate a bed from an inactive room.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.student_id} -> {self.bed}"


class HostelAttendance(models.Model):
    STATUS_CHOICES = (
        ('present', 'Present'),
        ('out_pass', 'Out Pass'),
        ('leave', 'Leave'),
        ('absent', 'Absent'),
    )

    allocation = models.ForeignKey(HostelAllocation, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='present')
    remarks = models.CharField(max_length=255, blank=True)
    marked_by = models.ForeignKey(NonTeachingStaff, on_delete=models.SET_NULL, null=True, blank=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('allocation', 'date')
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.allocation.student.student_id} {self.date} {self.status}"


class HostelIncident(models.Model):
    SEVERITY_CHOICES = (
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    )

    allocation = models.ForeignKey(HostelAllocation, on_delete=models.CASCADE, related_name='incidents')
    title = models.CharField(max_length=120)
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='low')
    occurred_at = models.DateTimeField()
    reported_by = models.ForeignKey(NonTeachingStaff, on_delete=models.SET_NULL, null=True, blank=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')
    resolved = models.BooleanField(default=False)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at', '-id']

    def __str__(self):
        return f"{self.allocation.student.student_id} - {self.title}"


class HostelInOutLog(models.Model):
    MOVEMENT_CHOICES = (
        ('in', 'IN'),
        ('out', 'OUT'),
    )

    allocation = models.ForeignKey(HostelAllocation, on_delete=models.CASCADE, related_name='in_out_logs')
    movement_type = models.CharField(max_length=5, choices=MOVEMENT_CHOICES)
    moved_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(NonTeachingStaff, on_delete=models.SET_NULL, null=True, blank=True)
    created_by_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-moved_at', '-id']

    def __str__(self):
        return f"{self.allocation.student.student_id} {self.movement_type.upper()} @ {self.moved_at}"
