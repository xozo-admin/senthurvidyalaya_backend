from django.db import models
from teachers.models import Teacher
from students.models import Student
from staff.models import NonTeachingStaff 
from django.core.exceptions import ValidationError


# 1. VEHICLE (Bus Details)
class Vehicle(models.Model):
    bus_number = models.CharField(max_length=20, unique=True, help_text="e.g., Bus 12")
    registration_number = models.CharField(max_length=20, unique=True, help_text="e.g., TN-01-AB-1234")
    capacity = models.IntegerField(help_text="Total Seats")
    
    # Driver Link
    driver = models.OneToOneField(
        NonTeachingStaff, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        limit_choices_to={'role': 'transport_staff'}, # Changed from 'Driver' to match your permissions
        related_name='assigned_bus'
    )

    def __str__(self):
        return f"{self.bus_number} ({self.registration_number})"

# 2. ROUTE (The Path + Live Tracking)
class Route(models.Model):
    vehicle = models.OneToOneField(Vehicle, on_delete=models.CASCADE, related_name='route')
    start_location = models.CharField(max_length=100)
    end_location = models.CharField(max_length=100)
    morning_start_location = models.CharField(max_length=100, blank=True, default='')
    morning_end_location = models.CharField(max_length=100, blank=True, default='')
    evening_start_location = models.CharField(max_length=100, blank=True, default='')
    evening_end_location = models.CharField(max_length=100, blank=True, default='')

    # --- LIVE TRACKING FIELDS ---
    is_active = models.BooleanField(default=False) # Is the trip currently running?
    current_latitude = models.FloatField(null=True, blank=True)
    current_longitude = models.FloatField(null=True, blank=True)
    current_speed = models.FloatField(null=True, blank=True, help_text="Speed in km/h")
    last_updated = models.DateTimeField(null=True, blank=True) # Timestamp of the last ping

    def __str__(self):
        return f"Route for {self.vehicle.bus_number}: {self.start_location} to {self.end_location}"

# 3. STOP (Checkpoints between Start and End)
class Stop(models.Model):
    TRIP_CHOICES = [
        ('Morning', 'Morning Pickup'),
        ('Evening', 'Evening Drop'),
    ]

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='stops')
    trip_type = models.CharField(max_length=10, choices=TRIP_CHOICES, default='Morning')
    stop_name = models.CharField(max_length=100)
    order_number = models.IntegerField(help_text="1 for first stop, 2 for second...")
    arrival_time = models.TimeField()
    
    # Coordinates for tracking/map (Static location of the stop)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['trip_type', 'order_number']

    def __str__(self):
        return f"{self.trip_type} {self.order_number}. {self.stop_name}"

# 4. ALLOCATION (Assigning People to Bus)
class TransportAllocation(models.Model):
    USER_TYPES = [('Student', 'Student'), ('Staff', 'Staff'), ('Teacher', 'Teacher')]
    
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    stop = models.ForeignKey(Stop, on_delete=models.SET_NULL, null=True, blank=True, related_name='allocations')
    user_type = models.CharField(max_length=10, choices=USER_TYPES)
    allocated_at = models.DateField(auto_now_add=True)
    
    # One of these will be filled
    student = models.OneToOneField(Student, on_delete=models.CASCADE, null=True, blank=True, related_name='transport_allocation')
    staff = models.OneToOneField(NonTeachingStaff, on_delete=models.CASCADE, null=True, blank=True, related_name='transport_allocation')
    teacher = models.OneToOneField(Teacher, on_delete=models.CASCADE, null=True, blank=True, related_name='transport_allocation')

def clean(self):
    filled = [self.student, self.teacher, self.staff]
    if sum(bool(x) for x in filled) != 1:
        raise ValidationError("Exactly one of student, teacher, or staff must be assigned.")

def save(self, *args, **kwargs):
    self.clean()
    super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.user_type} on {self.vehicle.bus_number}"

from django.db import models
from django.utils import timezone
from .models import TransportAllocation

class TransportAttendance(models.Model):
    # --- SIMPLIFIED OPTIONS ---
    STATUS_CHOICES = [
        ('Present', 'Present'),
        ('Absent', 'Absent'),  # Covers both "Leave" and "Missing"
    ]

    TRIP_CHOICES = [
        ('Morning', 'Morning Pickup'),
        ('Evening', 'Evening Drop'),
    ]

    allocation = models.ForeignKey(TransportAllocation, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(default=timezone.now)
    trip_type = models.CharField(max_length=10, choices=TRIP_CHOICES, default='Morning')
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Absent')
    
    # --- FIX IS HERE: Point to 'staff.NonTeachingStaff' ---
    marked_by = models.ForeignKey(
        'staff.NonTeachingStaff',  # <--- CORRECT NAME HERE
        on_delete=models.SET_NULL, 
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('allocation', 'date', 'trip_type')

    def __str__(self):
        return f"{self.allocation.student} - {self.status}"

#trasport proof

class TransportExpenseProof(models.Model):
    uploader = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE, related_name='transport_expenses')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=100)
    description = models.TextField()
    proof_file = models.FileField(upload_to='transport_proofs/') # Stores images/PDFs
    timestamp = models.DateTimeField(auto_now_add=True) # Auto-sets to current time on creation

    def __str__(self):
        return f"{self.vehicle.bus_number} - {self.title}"


class TransportArrivalAlertLog(models.Model):
    allocation = models.ForeignKey(
        TransportAllocation,
        on_delete=models.CASCADE,
        related_name='arrival_alert_logs',
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='arrival_alert_logs')
    stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='arrival_alert_logs')
    alert_date = models.DateField(default=timezone.localdate)
    threshold_km = models.FloatField(default=1.0)
    distance_km = models.FloatField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('allocation', 'route', 'stop', 'alert_date', 'threshold_km')
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.allocation_id} near {self.stop.stop_name} on {self.alert_date}"


class TransportWhatsAppAlertLog(models.Model):
    ALERT_BUS_STARTED = 'bus_started'
    ALERT_NEAR_STOP = 'near_stop'
    ALERT_CHOICES = [
        (ALERT_BUS_STARTED, 'Bus started'),
        (ALERT_NEAR_STOP, 'Near stop'),
    ]

    allocation = models.ForeignKey(
        TransportAllocation,
        on_delete=models.CASCADE,
        related_name='whatsapp_alert_logs',
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='whatsapp_alert_logs')
    stop = models.ForeignKey(
        Stop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='whatsapp_alert_logs',
    )
    alert_type = models.CharField(max_length=20, choices=ALERT_CHOICES)
    trip_type = models.CharField(max_length=10, choices=Stop.TRIP_CHOICES, default='Morning')
    alert_date = models.DateField(default=timezone.localdate)
    sent_to = models.CharField(max_length=30)
    status = models.CharField(max_length=20, default='PENDING')
    message = models.TextField(blank=True)
    response_data = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('allocation', 'route', 'stop', 'alert_type', 'trip_type', 'alert_date')
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.alert_type} - {self.allocation_id} - {self.alert_date}"
