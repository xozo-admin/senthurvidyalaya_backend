from django.db import models
from django.utils import timezone
# Import your specific NonTeachingStaff model
from staff.models import NonTeachingStaff 

class WorkOrder(models.Model):
    """
    Stores the 'What to do'.
    """
    # Matches the role choices in your Staff Model
    STAFF_TYPE_CHOICES = (
        ('admin_staff', 'Admin Staff'),
        ('finance_staff', 'Finance Staff'),
        ('it_staff', 'IT Staff'),
        ('operations_staff', 'Operations Staff'),
        ('transport_staff', 'Transport Staff'),
        ('external_staff', 'External Staff'),
    )

    description = models.TextField()
    staff_type = models.CharField(max_length=50, choices=STAFF_TYPE_CHOICES)
    created_date = models.DateField(default=timezone.now)
    is_recurring = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.description} ({self.created_date})"

class WorkAssignment(models.Model):
    """
    Links WorkOrder to a specific Staff Member.
    Stores Status and Proof.
    """
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='assignments')
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE, related_name='assigned_works')
    
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Completed', 'Completed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    
    # Proof Section
    proof_file = models.FileField(upload_to='staff_work_proofs/%Y/%m/%d/', null=True, blank=True)
    completion_note = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('work_order', 'staff') # Prevent duplicate assignment

    def __str__(self):
        return f"{self.staff.staff_id} - {self.status}"


# --- NEW MODEL: THE SCHEDULE ---
class RecurringTaskTemplate(models.Model):
    DAYS_OF_WEEK = (
        ('Monday', 'Monday'),
        ('Tuesday', 'Tuesday'),
        ('Wednesday', 'Wednesday'),
        ('Thursday', 'Thursday'),
        ('Friday', 'Friday'),
        ('Saturday', 'Saturday'),
        ('Sunday', 'Sunday'),
    )

    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.CASCADE)
    day_of_week = models.CharField(max_length=15, choices=DAYS_OF_WEEK)
    description = models.CharField(max_length=255)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent assigning same description to same staff on same day twice
        unique_together = ('staff', 'day_of_week', 'description')

    def __str__(self):
        return f"{self.day_of_week}: {self.staff.name} - {self.description}"