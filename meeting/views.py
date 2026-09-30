from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academics.models import ClassTeacher
from notifications.models import Notification
from notifications.whatsapp import send_whatsapp_to_user
from school.models import AcademicYear
from students.models import Enrollment, Student
from teachers.models import Teacher

from .models import (
    AdminMeetingRequest,
    MeetingReminder,
    TeacherMeeting,
    TeacherMeetingResponse,
    get_reminder_times,
)
from .permissions import (
    IsAdminUserType,
    IsStaffUserType,
    IsStudentUserType,
    IsTeacherOrStaffUserType,
    IsTeacherUserType,
)
from .serializers import (
    AdminMeetingApproveSerializer,
    AdminMeetingProposeSerializer,
    AdminMeetingRejectSerializer,
    AdminMeetingRequestCreateSerializer,
    AdminMeetingRequestSerializer,
    TeacherMeetingActionSerializer,
    TeacherMeetingCreateSerializer,
    TeacherMeetingResponseSerializer,
    TeacherMeetingSerializer,
)

User = get_user_model()


def _create_notification(recipient, sender, title, message, notification_type='Meeting'):
    Notification.objects.create(
        recipient=recipient,
        sender=sender,
        title=title,
        message=message,
        notification_type=notification_type,
    )
    send_whatsapp_to_user(recipient, f"{title}: {message}", notification_type=notification_type)


def _is_admin_staff_user(user):
    return bool(
        user
        and user.is_authenticated
        and user.user_type == 'staff'
        and getattr(getattr(user, 'staff_profile', None), 'role', None) == 'admin_staff'
    )


def _is_owner_admin_user(user):
    return bool(
        user
        and user.is_authenticated
        and user.user_type in ('admin', 'super_admin')
    )


def _admin_staff_target_queryset():
    return AdminMeetingRequest.objects.filter(
        admin__user_type='staff',
        admin__staff_profile__role='admin_staff',
    )


def _overlap_exists(start_dt, end_dt, queryset, start_field='start_at', end_field='end_at'):
    filters = {
        f'{start_field}__lt': end_dt,
        f'{end_field}__gt': start_dt,
    }
    return queryset.filter(**filters).exists()


def _create_admin_reminders(meeting_request):
    if not meeting_request.final_start:
        return

    MeetingReminder.objects.filter(
        admin_request=meeting_request,
        status=MeetingReminder.STATUS_PENDING,
    ).delete()

    reminder_times = get_reminder_times(meeting_request.final_start)
    recipients = [meeting_request.admin, meeting_request.requester]

    reminders = []
    for recipient in recipients:
        for remind_at in reminder_times:
            reminders.append(
                MeetingReminder(
                    meeting_type=MeetingReminder.TYPE_ADMIN,
                    admin_request=meeting_request,
                    recipient=recipient,
                    remind_at=remind_at,
                )
            )
    if reminders:
        MeetingReminder.objects.bulk_create(reminders)


def _create_teacher_reminders(meeting):
    MeetingReminder.objects.filter(
        teacher_meeting=meeting,
        status=MeetingReminder.STATUS_PENDING,
    ).delete()

    if meeting.status != TeacherMeeting.STATUS_CONFIRMED:
        return

    reminder_times = get_reminder_times(meeting.start_at)
    recipients = [meeting.teacher.user]
    if meeting.student.user:
        recipients.append(meeting.student.user)

    reminders = []
    for recipient in recipients:
        for remind_at in reminder_times:
            reminders.append(
                MeetingReminder(
                    meeting_type=MeetingReminder.TYPE_TEACHER,
                    teacher_meeting=meeting,
                    recipient=recipient,
                    remind_at=remind_at,
                )
            )

    if reminders:
        MeetingReminder.objects.bulk_create(reminders)


def _get_active_year():
    return AcademicYear.objects.filter(is_current=True).first()


def _get_teacher_profile_or_none(user):
    try:
        return user.teacher_profile
    except Teacher.DoesNotExist:
        return None
    except Exception:
        return None


def _teacher_can_manage_student(teacher, student):
    active_year = _get_active_year()
    if not active_year:
        return False, 'No active academic year configured.'

    class_teacher_record = ClassTeacher.objects.filter(
        teacher=teacher,
        academic_year=active_year,
    ).first()
    if not class_teacher_record:
        return False, 'You are not assigned as class teacher in the active year.'

    is_student_in_section = Enrollment.objects.filter(
        student=student,
        section=class_teacher_record.section,
        academic_year=active_year,
        is_active=True,
    ).exists()

    if not is_student_in_section:
        return False, 'Selected student is not in your assigned class section for active year.'

    return True, ''


# =========================
# Admin Meeting Request Flow
# =========================
class AdminMeetingRequestCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrStaffUserType]

    def post(self, request):
        serializer = AdminMeetingRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        requested_recipient = serializer.validated_data['admin']
        requester = request.user

        # Staff meeting routing:
        # - admin_staff -> owner admin
        # - other staff roles -> selected admin_staff recipient
        # Teacher flow remains unchanged (teacher -> selected admin).
        if requester.user_type == 'staff':
            requester_staff_role = getattr(getattr(requester, 'staff_profile', None), 'role', '')
            if requester_staff_role == 'admin_staff':
                admin_user = User.objects.filter(user_type='admin').order_by('id').first()
                if not admin_user:
                    return Response({'error': 'No admin user available to receive this request.'}, status=status.HTTP_400_BAD_REQUEST)
            else:
                if not (
                    requested_recipient.user_type == 'staff'
                    and getattr(getattr(requested_recipient, 'staff_profile', None), 'role', None) == 'admin_staff'
                ):
                    return Response({'error': 'Selected recipient must be an admin staff user.'}, status=status.HTTP_400_BAD_REQUEST)
                admin_user = requested_recipient
        else:
            admin_user = requested_recipient
            if not _is_owner_admin_user(admin_user):
                return Response({'error': 'Selected admin user is invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            meeting_request = serializer.save(requester=requester, admin=admin_user)

            _create_notification(
                recipient=admin_user,
                sender=requester,
                title='New Admin Meeting Request',
                message=f"{requester.username} requested a meeting: {meeting_request.subject}",
            )

        out = AdminMeetingRequestSerializer(meeting_request)
        return Response({'status': 201, 'data': out.data}, status=status.HTTP_201_CREATED)


class AdminStaffRecipientListView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUserType]

    def get(self, request):
        queryset = User.objects.filter(
            user_type='staff',
            staff_profile__role='admin_staff',
        ).select_related('staff_profile').order_by('staff_profile__name', 'username')

        data = []
        for user in queryset:
            staff_profile = getattr(user, 'staff_profile', None)
            data.append({
                'id': user.id,
                'username': user.username,
                'name': getattr(staff_profile, 'name', user.username),
                'staff_id': getattr(staff_profile, 'staff_id', ''),
                'role': getattr(staff_profile, 'role', ''),
            })

        return Response({'status': 200, 'count': len(data), 'data': data}, status=200)


class MyAdminMeetingRequestListView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrStaffUserType]

    def get(self, request):
        queryset = AdminMeetingRequest.objects.filter(requester=request.user).order_by('-created_at')
        serializer = AdminMeetingRequestSerializer(queryset, many=True)
        return Response({'status': 200, 'count': queryset.count(), 'data': serializer.data}, status=200)


class PendingAdminMeetingRequestListView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUserType]

    def get(self, request):
        if _is_owner_admin_user(request.user):
            queryset = AdminMeetingRequest.objects.filter(admin=request.user)
        elif _is_admin_staff_user(request.user):
            queryset = _admin_staff_target_queryset()
        else:
            queryset = AdminMeetingRequest.objects.none()
        status_filter = (request.query_params.get('status') or '').strip().upper()

        valid_statuses = {
            AdminMeetingRequest.STATUS_PENDING,
            AdminMeetingRequest.STATUS_APPROVED,
            AdminMeetingRequest.STATUS_REJECTED,
            AdminMeetingRequest.STATUS_RESCHEDULE_PROPOSED,
            AdminMeetingRequest.STATUS_CANCELLED,
        }

        if status_filter and status_filter != 'ALL':
            if status_filter not in valid_statuses:
                return Response(
                    {'error': 'Invalid status filter. Use ALL or a valid meeting status.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(status=status_filter)

        queryset = queryset.order_by('-created_at')
        serializer = AdminMeetingRequestSerializer(queryset, many=True)
        return Response({'status': 200, 'count': queryset.count(), 'data': serializer.data}, status=200)


class AdminMeetingApproveView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUserType]

    def patch(self, request, request_id):
        if _is_owner_admin_user(request.user):
            meeting_request = get_object_or_404(AdminMeetingRequest, id=request_id, admin=request.user)
        elif _is_admin_staff_user(request.user):
            meeting_request = get_object_or_404(_admin_staff_target_queryset(), id=request_id)
        else:
            return Response({'error': 'Unauthorized.'}, status=403)

        if meeting_request.status in [AdminMeetingRequest.STATUS_REJECTED, AdminMeetingRequest.STATUS_CANCELLED]:
            return Response({'error': 'Request already closed.'}, status=400)

        serializer = AdminMeetingApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        final_start = serializer.validated_data.get('final_start', meeting_request.preferred_start)
        duration_minutes = serializer.validated_data.get('duration_minutes', meeting_request.duration_minutes)
        final_end = final_start + timedelta(minutes=duration_minutes)

        if final_start <= timezone.now():
            return Response({'error': 'final_start must be in the future.'}, status=400)

        if _is_owner_admin_user(request.user):
            overlap_queryset = AdminMeetingRequest.objects.filter(
                admin=request.user,
                status=AdminMeetingRequest.STATUS_APPROVED,
            ).exclude(id=meeting_request.id)
        else:
            overlap_queryset = _admin_staff_target_queryset().filter(
                status=AdminMeetingRequest.STATUS_APPROVED,
            ).exclude(id=meeting_request.id)
        if _overlap_exists(final_start, final_end, overlap_queryset, 'final_start', 'final_end'):
            return Response({'error': 'This time overlaps with another approved admin meeting.'}, status=400)

        meeting_request.final_start = final_start
        meeting_request.final_end = final_end
        meeting_request.duration_minutes = duration_minutes
        meeting_request.admin_note = serializer.validated_data.get('admin_note', meeting_request.admin_note)
        meeting_request.status = AdminMeetingRequest.STATUS_APPROVED
        meeting_request.save()

        _create_admin_reminders(meeting_request)

        _create_notification(
            recipient=meeting_request.requester,
            sender=request.user,
            title='Admin Meeting Approved',
            message=f"Meeting '{meeting_request.subject}' approved for {final_start}.",
        )

        return Response({'status': 200, 'data': AdminMeetingRequestSerializer(meeting_request).data}, status=200)


class AdminMeetingRejectView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUserType]

    def patch(self, request, request_id):
        if _is_owner_admin_user(request.user):
            meeting_request = get_object_or_404(AdminMeetingRequest, id=request_id, admin=request.user)
        elif _is_admin_staff_user(request.user):
            meeting_request = get_object_or_404(_admin_staff_target_queryset(), id=request_id)
        else:
            return Response({'error': 'Unauthorized.'}, status=403)
        serializer = AdminMeetingRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        meeting_request.status = AdminMeetingRequest.STATUS_REJECTED
        meeting_request.admin_note = serializer.validated_data.get('admin_note', '')
        meeting_request.save(update_fields=['status', 'admin_note', 'updated_at'])

        _create_notification(
            recipient=meeting_request.requester,
            sender=request.user,
            title='Admin Meeting Rejected',
            message=f"Meeting '{meeting_request.subject}' was rejected.",
        )

        return Response({'status': 200, 'data': AdminMeetingRequestSerializer(meeting_request).data}, status=200)


class AdminMeetingProposeTimeView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUserType]

    def patch(self, request, request_id):
        if _is_owner_admin_user(request.user):
            meeting_request = get_object_or_404(AdminMeetingRequest, id=request_id, admin=request.user)
        elif _is_admin_staff_user(request.user):
            meeting_request = get_object_or_404(_admin_staff_target_queryset(), id=request_id)
        else:
            return Response({'error': 'Unauthorized.'}, status=403)
        serializer = AdminMeetingProposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        proposed_start = serializer.validated_data['proposed_start']
        if proposed_start <= timezone.now():
            return Response({'error': 'proposed_start must be in the future.'}, status=400)

        duration_minutes = serializer.validated_data.get('duration_minutes', meeting_request.duration_minutes)
        proposed_end = proposed_start + timedelta(minutes=duration_minutes)

        if _is_owner_admin_user(request.user):
            overlap_queryset = AdminMeetingRequest.objects.filter(
                admin=request.user,
                status=AdminMeetingRequest.STATUS_APPROVED,
            ).exclude(id=meeting_request.id)
        else:
            overlap_queryset = _admin_staff_target_queryset().filter(
                status=AdminMeetingRequest.STATUS_APPROVED,
            ).exclude(id=meeting_request.id)
        if _overlap_exists(proposed_start, proposed_end, overlap_queryset, 'final_start', 'final_end'):
            return Response({'error': 'Proposed time overlaps with another approved admin meeting.'}, status=400)

        meeting_request.status = AdminMeetingRequest.STATUS_RESCHEDULE_PROPOSED
        meeting_request.final_start = proposed_start
        meeting_request.final_end = proposed_end
        meeting_request.duration_minutes = duration_minutes
        meeting_request.admin_note = serializer.validated_data.get('admin_note', '')
        meeting_request.save()

        _create_notification(
            recipient=meeting_request.requester,
            sender=request.user,
            title='Admin Proposed New Meeting Time',
            message=f"New proposed time for '{meeting_request.subject}' is {proposed_start}.",
        )

        return Response({'status': 200, 'data': AdminMeetingRequestSerializer(meeting_request).data}, status=200)


class RequesterAdminMeetingRescheduleView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrStaffUserType]

    def patch(self, request, request_id):
        meeting_request = get_object_or_404(AdminMeetingRequest, id=request_id, requester=request.user)
        serializer = AdminMeetingProposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if meeting_request.status in [AdminMeetingRequest.STATUS_REJECTED, AdminMeetingRequest.STATUS_CANCELLED]:
            return Response({'error': 'Request already closed.'}, status=400)

        proposed_start = serializer.validated_data['proposed_start']
        if proposed_start <= timezone.now():
            return Response({'error': 'proposed_start must be in the future.'}, status=400)

        duration_minutes = serializer.validated_data.get('duration_minutes', meeting_request.duration_minutes)
        proposed_end = proposed_start + timedelta(minutes=duration_minutes)

        overlap_queryset = AdminMeetingRequest.objects.filter(
            admin=meeting_request.admin,
            status=AdminMeetingRequest.STATUS_APPROVED,
        ).exclude(id=meeting_request.id)
        if _overlap_exists(proposed_start, proposed_end, overlap_queryset, 'final_start', 'final_end'):
            return Response({'error': 'Proposed time overlaps with another approved admin meeting.'}, status=400)

        requester_note = serializer.validated_data.get('admin_note', '').strip()
        if requester_note:
            meeting_request.admin_note = f"Requester reschedule note: {requester_note}"

        meeting_request.status = AdminMeetingRequest.STATUS_RESCHEDULE_PROPOSED
        meeting_request.final_start = proposed_start
        meeting_request.final_end = proposed_end
        meeting_request.duration_minutes = duration_minutes
        meeting_request.save()

        _create_notification(
            recipient=meeting_request.admin,
            sender=request.user,
            title='Requester Asked to Reschedule',
            message=f"Requester asked new time for '{meeting_request.subject}' -> {proposed_start}.",
        )

        return Response({'status': 200, 'data': AdminMeetingRequestSerializer(meeting_request).data}, status=200)


# =========================
# Teacher -> Student/Parent Flow
# =========================
class TeacherMeetingCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherUserType]

    def post(self, request):
        teacher = _get_teacher_profile_or_none(request.user)
        if not teacher:
            return Response({'error': 'Teacher profile not found.'}, status=404)

        payload = request.data.copy()
        student_value = payload.get('student')
        student_id_code = payload.get('student_id')

        if student_id_code and not student_value:
            student_obj = Student.objects.filter(student_id=student_id_code).first()
            if not student_obj:
                return Response({'error': 'Invalid student_id provided.'}, status=400)
            payload['student'] = student_obj.id
        elif isinstance(student_value, str) and not student_value.isdigit():
            student_obj = Student.objects.filter(student_id=student_value).first()
            if not student_obj:
                return Response({'error': 'Invalid student identifier provided.'}, status=400)
            payload['student'] = student_obj.id

        serializer = TeacherMeetingCreateSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        student = serializer.validated_data['student']
        allowed, reason = _teacher_can_manage_student(teacher, student)
        if not allowed:
            return Response({'error': reason}, status=403)

        start_at = serializer.validated_data['start_at']
        end_at = serializer.validated_data['end_at']

        teacher_overlap = TeacherMeeting.objects.filter(
            teacher=teacher,
            status__in=[
                TeacherMeeting.STATUS_REQUESTED,
                TeacherMeeting.STATUS_CONFIRMED,
                TeacherMeeting.STATUS_RESCHEDULE_REQUESTED,
            ],
        )
        if _overlap_exists(start_at, end_at, teacher_overlap):
            return Response({'error': 'Meeting overlaps with another teacher meeting.'}, status=400)

        student_overlap = TeacherMeeting.objects.filter(
            student=student,
            status__in=[
                TeacherMeeting.STATUS_REQUESTED,
                TeacherMeeting.STATUS_CONFIRMED,
                TeacherMeeting.STATUS_RESCHEDULE_REQUESTED,
            ],
        )
        if _overlap_exists(start_at, end_at, student_overlap):
            return Response({'error': 'Meeting overlaps with another student meeting.'}, status=400)

        meeting = serializer.save(
            teacher=teacher,
            created_by=request.user,
            status=TeacherMeeting.STATUS_REQUESTED,
        )

        if student.user:
            _create_notification(
                recipient=student.user,
                sender=request.user,
                title='New Teacher Meeting',
                message=f"{teacher.name} scheduled a meeting: {meeting.subject}",
            )

        return Response({'status': 201, 'data': TeacherMeetingSerializer(meeting).data}, status=201)


class TeacherMeetingTeacherListView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherUserType]

    def get(self, request):
        teacher = _get_teacher_profile_or_none(request.user)
        if not teacher:
            return Response({'error': 'Teacher profile not found.'}, status=404)

        queryset = TeacherMeeting.objects.filter(teacher=teacher).order_by('-start_at')
        serializer = TeacherMeetingSerializer(queryset, many=True)
        return Response({'status': 200, 'count': queryset.count(), 'data': serializer.data}, status=200)


class StudentMeetingInboxView(APIView):
    permission_classes = [IsAuthenticated, IsStudentUserType]

    def get(self, request):
        try:
            student = request.user.student_profile
        except Student.DoesNotExist:
            return Response({'error': 'Student profile not found.'}, status=404)

        queryset = TeacherMeeting.objects.filter(student=student).order_by('-start_at')
        serializer = TeacherMeetingSerializer(queryset, many=True)
        return Response({'status': 200, 'count': queryset.count(), 'data': serializer.data}, status=200)


class StudentMeetingRespondView(APIView):
    permission_classes = [IsAuthenticated, IsStudentUserType]

    def patch(self, request, meeting_id):
        meeting = get_object_or_404(TeacherMeeting, id=meeting_id)

        try:
            student = request.user.student_profile
        except Student.DoesNotExist:
            return Response({'error': 'Student profile not found.'}, status=404)

        if meeting.student_id != student.id:
            return Response({'error': 'Access denied.'}, status=403)

        serializer = TeacherMeetingResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        actor_type = serializer.validated_data.get('actor_type', TeacherMeetingResponse.ACTOR_STUDENT)
        response_value = serializer.validated_data['response']

        response_obj, _ = TeacherMeetingResponse.objects.update_or_create(
            meeting=meeting,
            actor_type=actor_type,
            defaults={
                'student': student,
                'response': response_value,
                'comment': serializer.validated_data.get('comment', ''),
                'proposed_start': serializer.validated_data.get('proposed_start'),
                'proposed_end': serializer.validated_data.get('proposed_end'),
            },
        )

        if response_value == TeacherMeetingResponse.RESPONSE_ACCEPT:
            meeting.status = TeacherMeeting.STATUS_CONFIRMED
        elif response_value == TeacherMeetingResponse.RESPONSE_RESCHEDULE:
            meeting.status = TeacherMeeting.STATUS_RESCHEDULE_REQUESTED
            proposed_start = serializer.validated_data.get('proposed_start')
            proposed_end = serializer.validated_data.get('proposed_end')
            if proposed_start and proposed_end:
                meeting.start_at = proposed_start
                meeting.end_at = proposed_end
                meeting.save(update_fields=['status', 'start_at', 'end_at', 'updated_at'])
            else:
                meeting.save(update_fields=['status', 'updated_at'])
        else:
            meeting.save(update_fields=['status', 'updated_at'])

        _create_teacher_reminders(meeting)

        _create_notification(
            recipient=meeting.teacher.user,
            sender=request.user,
            title='Teacher Meeting Response',
            message=f"{actor_type} response: {response_value} for meeting '{meeting.subject}'.",
        )

        return Response(
            {
                'status': 200,
                'meeting': TeacherMeetingSerializer(meeting).data,
                'response': TeacherMeetingResponseSerializer(response_obj).data,
            },
            status=200,
        )


class TeacherMeetingActionView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherUserType]

    def patch(self, request, meeting_id):
        teacher = _get_teacher_profile_or_none(request.user)
        if not teacher:
            return Response({'error': 'Teacher profile not found.'}, status=404)

        meeting = get_object_or_404(TeacherMeeting, id=meeting_id, teacher=teacher)
        serializer = TeacherMeetingActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data['action']
        note = serializer.validated_data.get('note', '')

        if action == 'confirm':
            meeting.status = TeacherMeeting.STATUS_CONFIRMED
            meeting.save(update_fields=['status', 'updated_at'])
            _create_teacher_reminders(meeting)

        elif action == 'cancel':
            meeting.status = TeacherMeeting.STATUS_CANCELLED
            meeting.save(update_fields=['status', 'updated_at'])
            MeetingReminder.objects.filter(teacher_meeting=meeting, status=MeetingReminder.STATUS_PENDING).delete()

        elif action == 'complete':
            meeting.status = TeacherMeeting.STATUS_COMPLETED
            meeting.save(update_fields=['status', 'updated_at'])

        elif action == 'reschedule':
            start_at = serializer.validated_data['start_at']
            end_at = serializer.validated_data['end_at']

            teacher_overlap = TeacherMeeting.objects.filter(
                teacher=teacher,
                status__in=[
                    TeacherMeeting.STATUS_REQUESTED,
                    TeacherMeeting.STATUS_CONFIRMED,
                    TeacherMeeting.STATUS_RESCHEDULE_REQUESTED,
                ],
            ).exclude(id=meeting.id)
            if _overlap_exists(start_at, end_at, teacher_overlap):
                return Response({'error': 'Rescheduled time overlaps with another teacher meeting.'}, status=400)

            student_overlap = TeacherMeeting.objects.filter(
                student=meeting.student,
                status__in=[
                    TeacherMeeting.STATUS_REQUESTED,
                    TeacherMeeting.STATUS_CONFIRMED,
                    TeacherMeeting.STATUS_RESCHEDULE_REQUESTED,
                ],
            ).exclude(id=meeting.id)
            if _overlap_exists(start_at, end_at, student_overlap):
                return Response({'error': 'Rescheduled time overlaps with another student meeting.'}, status=400)

            meeting.start_at = start_at
            meeting.end_at = end_at
            meeting.status = TeacherMeeting.STATUS_REQUESTED
            meeting.save(update_fields=['start_at', 'end_at', 'status', 'updated_at'])
            MeetingReminder.objects.filter(teacher_meeting=meeting, status=MeetingReminder.STATUS_PENDING).delete()

        if meeting.student.user:
            _create_notification(
                recipient=meeting.student.user,
                sender=request.user,
                title='Teacher Meeting Updated',
                message=f"Meeting '{meeting.subject}' updated. Action: {action}. {note}".strip(),
            )

        return Response({'status': 200, 'data': TeacherMeetingSerializer(meeting).data}, status=200)


# =========================
# Reminder Dispatch (Cron-safe endpoint)
# =========================
class TriggerDueMeetingRemindersView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUserType]

    def post(self, request):
        now = timezone.now()

        due = MeetingReminder.objects.filter(
            status=MeetingReminder.STATUS_PENDING,
            remind_at__lte=now,
            channel=MeetingReminder.CHANNEL_IN_APP,
        ).select_related('recipient', 'admin_request', 'teacher_meeting')

        sent_count = 0
        failed_count = 0

        for reminder in due:
            try:
                if reminder.meeting_type == MeetingReminder.TYPE_ADMIN and reminder.admin_request:
                    title = 'Admin Meeting Reminder'
                    when = reminder.admin_request.final_start or reminder.admin_request.preferred_start
                    message = f"Reminder: '{reminder.admin_request.subject}' at {when}."
                elif reminder.meeting_type == MeetingReminder.TYPE_TEACHER and reminder.teacher_meeting:
                    title = 'Teacher Meeting Reminder'
                    message = f"Reminder: '{reminder.teacher_meeting.subject}' at {reminder.teacher_meeting.start_at}."
                else:
                    reminder.status = MeetingReminder.STATUS_FAILED
                    reminder.error_message = 'Invalid reminder target.'
                    reminder.sent_at = timezone.now()
                    reminder.save(update_fields=['status', 'error_message', 'sent_at', 'updated_at'])
                    failed_count += 1
                    continue

                _create_notification(
                    recipient=reminder.recipient,
                    sender=request.user,
                    title=title,
                    message=message,
                    notification_type='Meeting Reminder',
                )

                reminder.status = MeetingReminder.STATUS_SENT
                reminder.sent_at = timezone.now()
                reminder.save(update_fields=['status', 'sent_at', 'updated_at'])
                sent_count += 1

            except Exception as exc:
                reminder.status = MeetingReminder.STATUS_FAILED
                reminder.error_message = str(exc)
                reminder.sent_at = timezone.now()
                reminder.save(update_fields=['status', 'error_message', 'sent_at', 'updated_at'])
                failed_count += 1

        return Response(
            {
                'status': 200,
                'processed': due.count(),
                'sent': sent_count,
                'failed': failed_count,
            },
            status=200,
        )
