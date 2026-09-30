from django.db import models
from django.conf import settings  # To refer to your Custom User model
from school.models import School

STAFF_PERMISSION_LABELS = {
    'home': 'Home',
    'actions': 'Actions',
    'announcements': 'Notices',
    'profile': 'Profile',
    'meetings': 'Meetings',
    'attendance': 'Attendance',
    'tasks': 'Tasks',
    'salary': 'Salary',
    'inventory': 'Inventory',
    'transport_expenses': 'Transport Expenses',
    'route_map': 'Route Map',
    'notifications': 'Notifications',
}

STAFF_PERMISSION_GROUPS = {
    'bottom': ['home', 'actions', 'announcements', 'profile'],
    'common': ['meetings', 'attendance', 'tasks', 'salary', 'inventory', 'notifications'],
    'transport': ['transport_expenses', 'route_map'],
}


def default_staff_role_permissions():
    return {key: True for key in STAFF_PERMISSION_LABELS}


class NonTeachingStaff(models.Model):
    # 1. The staff role categories
    ROLE_CHOICES = (
        ('admin_staff', 'Admin Staff'),
        ('finance_staff', 'Finance Staff'),
        ('it_staff', 'IT Staff'),
        ('operations_staff', 'Operations Staff'),
        ('hostel_warden', 'Hostel Warden'),
        ('transport_staff', 'Transport Staff'),
        ('external_staff', 'External Staff'),
    )

    # 2. Link to User Account (CRITICAL for Login)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='staff_profile',
        null=True  # Null=True allowed for safe migration, but should be filled
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='staff_members',
        null=True,
        blank=True,
    )

    # 3. Basic Details
    staff_id = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    joining_date = models.DateField(null=True, blank=True)
    profile_image = models.ImageField(upload_to='profiles/staff/', blank=True, null=True)
    
    # 4. The Role Category
    role = models.CharField(max_length=50, choices=ROLE_CHOICES) 
    
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(default="Not Provided")
    extra_details = models.JSONField(default=dict, blank=True)
    
    bank_account_number = models.CharField(max_length=30, blank=True, null=True)
    ifsc_code = models.CharField(max_length=15, blank=True, null=True)
    account_holder_name = models.CharField(max_length=100, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    upi_id = models.CharField(max_length=50, blank=True, null=True)
    

    def __str__(self):
        return f"{self.name} - {self.get_role_display()} ({self.staff_id})"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school', 'staff_id'], name='unique_staff_id_per_school')
        ]

    def normalized_role_permissions(self):
        if self.school_id and self.role:
            try:
                return StaffRolePermission.objects.get(
                    school=self.school,
                    role=self.role,
                ).normalized_role_permissions()
            except StaffRolePermission.DoesNotExist:
                pass
        return default_staff_role_permissions()


class StaffRolePermission(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='staff_role_permissions',
    )
    role = models.CharField(max_length=50, choices=NonTeachingStaff.ROLE_CHOICES)
    role_permissions = models.JSONField(default=default_staff_role_permissions, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school', 'role')
        ordering = ['role']

    def normalized_role_permissions(self):
        permissions = default_staff_role_permissions()
        if isinstance(self.role_permissions, dict):
            for key in permissions:
                if key in self.role_permissions:
                    permissions[key] = bool(self.role_permissions[key])
        return permissions

    def __str__(self):
        return f"{self.school.name} - {self.get_role_display()} permissions"
