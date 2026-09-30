from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    USER_TYPES = (
        ('super_admin', 'Super Admin'),
        ('admin', 'Admin'),
        ('teacher', 'Teacher'),
        ('student', 'Student'),
        ('staff', 'Non Teaching Staff'), # Used 'staff' key to match your views.py logic
    )

    # username = login id (already provided by AbstractUser)
    user_type = models.CharField(max_length=20, choices=USER_TYPES, default='student')
    phone = models.CharField(max_length=15, null=True, blank=True)
    
    # --- ENCRYPTION FIELD ---
    # Stores the session key for AES-GCM encryption
    session_key = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.username} ({self.user_type})"


from django.utils import timezone
import random

# ... keep your existing User model ...

class PasswordResetOTP(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now=True)

    def is_valid(self):
        # OTP is valid for 10 minutes
        now = timezone.now()
        diff = now - self.created_at
        return diff.total_seconds() < 600  # 600 seconds = 10 minutes
