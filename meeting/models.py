from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from students.models import Student
from teachers.models import Teacher


class AdminMeetingRequest(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_RESCHEDULE_PROPOSED = 'RESCHEDULE_PROPOSED'
    STATUS_CANCELLED = 'CANCELLED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_RESCHEDULE_PROPOSED, 'Reschedule Proposed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_meeting_requests_made',
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_meeting_requests_received',
    )
    subject = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    preferred_start = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=30)

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_PENDING)
    admin_note = models.TextField(blank=True)
    final_start = models.DateTimeField(null=True, blank=True)
    final_end = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['admin', 'status']),
            models.Index(fields=['requester', 'status']),
        ]

    def __str__(self):
        return f"{self.requester} -> {self.admin} [{self.status}]"


class TeacherMeeting(models.Model):
    MODE_ONLINE = 'ONLINE'
    MODE_OFFLINE = 'OFFLINE'

    MODE_CHOICES = [
        (MODE_ONLINE, 'Online'),
        (MODE_OFFLINE, 'Offline'),
    ]

    STATUS_REQUESTED = 'REQUESTED'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_RESCHEDULE_REQUESTED = 'RESCHEDULE_REQUESTED'
    STATUS_DECLINED = 'DECLINED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_COMPLETED = 'COMPLETED'

    STATUS_CHOICES = [
        (STATUS_REQUESTED, 'Requested'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_RESCHEDULE_REQUESTED, 'Reschedule Requested'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='meetings')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='teacher_meetings')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='teacher_meetings_created',
    )

    subject = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_OFFLINE)
    meeting_link = models.URLField(blank=True)
    location = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_REQUESTED)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_at']
        indexes = [
            models.Index(fields=['teacher', 'status']),
            models.Index(fields=['student', 'status']),
            models.Index(fields=['start_at']),
        ]

    def __str__(self):
        return f"{self.teacher} with {self.student} [{self.status}]"


class TeacherMeetingResponse(models.Model):
    ACTOR_STUDENT = 'STUDENT'
    ACTOR_PARENT = 'PARENT'

    ACTOR_CHOICES = [
        (ACTOR_STUDENT, 'Student'),
        (ACTOR_PARENT, 'Parent'),
    ]

    RESPONSE_ACCEPT = 'ACCEPT'
    RESPONSE_DECLINE = 'DECLINE'
    RESPONSE_RESCHEDULE = 'RESCHEDULE'

    RESPONSE_CHOICES = [
        (RESPONSE_ACCEPT, 'Accept'),
        (RESPONSE_DECLINE, 'Decline'),
        (RESPONSE_RESCHEDULE, 'Reschedule'),
    ]

    meeting = models.ForeignKey(TeacherMeeting, on_delete=models.CASCADE, related_name='responses')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='meeting_responses')

    actor_type = models.CharField(max_length=10, choices=ACTOR_CHOICES)
    response = models.CharField(max_length=12, choices=RESPONSE_CHOICES)

    comment = models.TextField(blank=True)
    proposed_start = models.DateTimeField(null=True, blank=True)
    proposed_end = models.DateTimeField(null=True, blank=True)

    responded_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('meeting', 'actor_type')
        ordering = ['-responded_at']

    def __str__(self):
        return f"{self.meeting_id} {self.actor_type} {self.response}"


class MeetingReminder(models.Model):
    TYPE_ADMIN = 'ADMIN_REQUEST'
    TYPE_TEACHER = 'TEACHER_MEETING'

    TYPE_CHOICES = [
        (TYPE_ADMIN, 'Admin Request'),
        (TYPE_TEACHER, 'Teacher Meeting'),
    ]

    CHANNEL_IN_APP = 'IN_APP'
    CHANNEL_EMAIL = 'EMAIL'
    CHANNEL_SMS = 'SMS'

    CHANNEL_CHOICES = [
        (CHANNEL_IN_APP, 'In App'),
        (CHANNEL_EMAIL, 'Email'),
        (CHANNEL_SMS, 'SMS'),
    ]

    STATUS_PENDING = 'PENDING'
    STATUS_SENT = 'SENT'
    STATUS_FAILED = 'FAILED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
    ]

    meeting_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    admin_request = models.ForeignKey(
        AdminMeetingRequest,
        on_delete=models.CASCADE,
        related_name='reminders',
        null=True,
        blank=True,
    )
    teacher_meeting = models.ForeignKey(
        TeacherMeeting,
        on_delete=models.CASCADE,
        related_name='reminders',
        null=True,
        blank=True,
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='meeting_reminders',
    )
    remind_at = models.DateTimeField()
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default=CHANNEL_IN_APP)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['remind_at']
        indexes = [
            models.Index(fields=['status', 'remind_at']),
            models.Index(fields=['recipient', 'status']),
        ]

    def clean(self):
        if self.meeting_type == self.TYPE_ADMIN and not self.admin_request:
            raise ValueError('admin_request is required for TYPE_ADMIN reminders.')
        if self.meeting_type == self.TYPE_TEACHER and not self.teacher_meeting:
            raise ValueError('teacher_meeting is required for TYPE_TEACHER reminders.')

    def __str__(self):
        return f"{self.meeting_type} -> {self.recipient} @ {self.remind_at}"


def get_reminder_times(start_at):
    """Create 24-hour and 1-hour reminder times if still in the future."""
    now = timezone.now()
    candidates = [
        start_at - timedelta(hours=24),
        start_at - timedelta(hours=1),
    ]
    return [dt for dt in candidates if dt > now]
