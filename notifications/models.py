from django.db import models
from django.conf import settings

class Notification(models.Model):
    # 1. Who gets this? (Link to User table)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='notifications_received'
    )
    
    # 2. Who sent this? (Link to User table - Optional/Null allowed)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='notifications_sent'
    )
    
    # 3. Content
    title = models.CharField(max_length=255)
    message = models.TextField()
    
    # 4. Metadata
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True) 
    
    # 5. Type (Helps frontend show icons: "Assignment", "Transport", "Fees")
    notification_type = models.CharField(max_length=50, default='General') 

    def __str__(self):
        sender_name = self.sender.username if self.sender else "System"
        return f"From {sender_name} to {self.recipient.username}: {self.title}"

#fcm for notification store
class FCMDevice(models.Model):
    # Change 'User' to 'settings.AUTH_USER_MODEL'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='fcm_devices')
    fcm_token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        # We also need to be careful with __str__ just in case
        return f"Device for {self.user}"


class WhatsAppAlertSetting(models.Model):
    ALERT_FEES = 'fees'
    ALERT_MEETINGS = 'meetings'
    ALERT_ASSIGNMENTS = 'assignments'
    ALERT_LEAVE = 'leave'
    ALERT_EXAMS = 'exams'
    ALERT_TRANSPORT = 'transport'
    ALERT_ATTENDANCE = 'attendance'

    ALERT_CHOICES = [
        (ALERT_FEES, 'Fee reminders'),
        (ALERT_MEETINGS, 'Meeting alerts'),
        (ALERT_ASSIGNMENTS, 'Assignment alerts'),
        (ALERT_LEAVE, 'Leave alerts'),
        (ALERT_EXAMS, 'Exam and mark-change alerts'),
        (ALERT_TRANSPORT, 'Transport alerts'),
        (ALERT_ATTENDANCE, 'Attendance alerts'),
    ]

    alert_type = models.CharField(max_length=30, choices=ALERT_CHOICES, unique=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['alert_type']

    def __str__(self):
        return f"{self.get_alert_type_display()}: {'Enabled' if self.enabled else 'Disabled'}"
