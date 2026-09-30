from django.utils import timezone
from rest_framework import serializers

from .models import (
    AdminMeetingRequest,
    TeacherMeeting,
    TeacherMeetingResponse,
    MeetingReminder,
)


class AdminMeetingRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminMeetingRequest
        fields = [
            'id',
            'admin',
            'subject',
            'message',
            'preferred_start',
            'duration_minutes',
            'status',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'created_at']

    def validate_preferred_start(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('preferred_start must be in the future.')
        return value

    def validate_duration_minutes(self, value):
        if value <= 0 or value > 240:
            raise serializers.ValidationError('duration_minutes must be between 1 and 240.')
        return value


class AdminMeetingRequestSerializer(serializers.ModelSerializer):
    requester_username = serializers.CharField(source='requester.username', read_only=True)
    admin_username = serializers.CharField(source='admin.username', read_only=True)
    requester_name = serializers.SerializerMethodField()
    requester_id_code = serializers.SerializerMethodField()
    requester_user_type = serializers.CharField(source='requester.user_type', read_only=True)

    def get_requester_name(self, obj):
        user = obj.requester
        try:
            if user.user_type == 'teacher' and user.teacher_profile:
                return user.teacher_profile.name
        except Exception:
            pass
        try:
            if user.user_type == 'staff' and user.staff_profile:
                return user.staff_profile.name
        except Exception:
            pass
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.username

    def get_requester_id_code(self, obj):
        user = obj.requester
        try:
            if user.user_type == 'teacher' and user.teacher_profile:
                return user.teacher_profile.teacher_id
        except Exception:
            pass
        try:
            if user.user_type == 'staff' and user.staff_profile:
                return user.staff_profile.staff_id
        except Exception:
            pass
        return user.username

    class Meta:
        model = AdminMeetingRequest
        fields = '__all__'


class AdminMeetingApproveSerializer(serializers.Serializer):
    final_start = serializers.DateTimeField(required=False)
    duration_minutes = serializers.IntegerField(required=False, min_value=1, max_value=240)
    admin_note = serializers.CharField(required=False, allow_blank=True)


class AdminMeetingRejectSerializer(serializers.Serializer):
    admin_note = serializers.CharField(required=False, allow_blank=True)


class AdminMeetingProposeSerializer(serializers.Serializer):
    proposed_start = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(required=False, min_value=1, max_value=240)
    admin_note = serializers.CharField(required=False, allow_blank=True)


class TeacherMeetingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherMeeting
        fields = [
            'id',
            'student',
            'subject',
            'description',
            'start_at',
            'end_at',
            'mode',
            'meeting_link',
            'location',
            'status',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'created_at']

    def validate(self, attrs):
        start_at = attrs.get('start_at')
        end_at = attrs.get('end_at')

        if start_at <= timezone.now():
            raise serializers.ValidationError({'start_at': 'Meeting start must be in the future.'})
        if end_at <= start_at:
            raise serializers.ValidationError({'end_at': 'end_at must be after start_at.'})

        mode = attrs.get('mode')
        if mode == TeacherMeeting.MODE_ONLINE and not attrs.get('meeting_link'):
            raise serializers.ValidationError({'meeting_link': 'meeting_link is required for online meetings.'})
        if mode == TeacherMeeting.MODE_OFFLINE and not attrs.get('location'):
            raise serializers.ValidationError({'location': 'location is required for offline meetings.'})

        return attrs


class TeacherMeetingSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    student_name = serializers.CharField(source='student.student_name', read_only=True)
    student_id_code = serializers.CharField(source='student.student_id', read_only=True)

    class Meta:
        model = TeacherMeeting
        fields = '__all__'


class TeacherMeetingResponseSerializer(serializers.ModelSerializer):
    actor_type = serializers.ChoiceField(
        choices=TeacherMeetingResponse.ACTOR_CHOICES,
        required=False,
        default=TeacherMeetingResponse.ACTOR_STUDENT,
    )
    response = serializers.ChoiceField(
        choices=[
            TeacherMeetingResponse.RESPONSE_ACCEPT,
            TeacherMeetingResponse.RESPONSE_RESCHEDULE,
        ]
    )

    class Meta:
        model = TeacherMeetingResponse
        fields = [
            'id',
            'actor_type',
            'response',
            'comment',
            'proposed_start',
            'proposed_end',
            'responded_at',
        ]
        read_only_fields = ['id', 'responded_at']

    def validate(self, attrs):
        response = attrs.get('response')
        proposed_start = attrs.get('proposed_start')
        proposed_end = attrs.get('proposed_end')

        if response == TeacherMeetingResponse.RESPONSE_RESCHEDULE:
            if not proposed_start or not proposed_end:
                raise serializers.ValidationError('proposed_start and proposed_end are required for RESCHEDULE.')
            if proposed_start <= timezone.now():
                raise serializers.ValidationError('proposed_start must be in the future.')
            if proposed_end <= proposed_start:
                raise serializers.ValidationError('proposed_end must be after proposed_start.')

        return attrs


class TeacherMeetingActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['confirm', 'cancel', 'complete', 'reschedule'])
    start_at = serializers.DateTimeField(required=False)
    end_at = serializers.DateTimeField(required=False)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        action = attrs.get('action')
        start_at = attrs.get('start_at')
        end_at = attrs.get('end_at')

        if action == 'reschedule':
            if not start_at or not end_at:
                raise serializers.ValidationError('start_at and end_at are required for reschedule action.')
            if start_at <= timezone.now():
                raise serializers.ValidationError('start_at must be in the future.')
            if end_at <= start_at:
                raise serializers.ValidationError('end_at must be after start_at.')

        return attrs


class MeetingReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingReminder
        fields = '__all__'
