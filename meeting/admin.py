from django.contrib import admin

from .models import AdminMeetingRequest, MeetingReminder, TeacherMeeting, TeacherMeetingResponse


@admin.register(AdminMeetingRequest)
class AdminMeetingRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'requester', 'admin', 'subject', 'status', 'preferred_start', 'final_start', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('subject', 'requester__username', 'admin__username')


@admin.register(TeacherMeeting)
class TeacherMeetingAdmin(admin.ModelAdmin):
    list_display = ('id', 'teacher', 'student', 'subject', 'status', 'start_at', 'end_at', 'created_at')
    list_filter = ('status', 'mode', 'created_at')
    search_fields = ('subject', 'teacher__name', 'student__student_name')


@admin.register(TeacherMeetingResponse)
class TeacherMeetingResponseAdmin(admin.ModelAdmin):
    list_display = ('id', 'meeting', 'student', 'actor_type', 'response', 'responded_at')
    list_filter = ('actor_type', 'response', 'responded_at')


@admin.register(MeetingReminder)
class MeetingReminderAdmin(admin.ModelAdmin):
    list_display = ('id', 'meeting_type', 'recipient', 'remind_at', 'status', 'channel', 'sent_at')
    list_filter = ('meeting_type', 'status', 'channel')
    search_fields = ('recipient__username',)
