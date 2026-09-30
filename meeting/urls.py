from django.urls import path

from .views import (
    AdminMeetingApproveView,
    AdminStaffRecipientListView,
    AdminMeetingProposeTimeView,
    AdminMeetingRejectView,
    AdminMeetingRequestCreateView,
    MyAdminMeetingRequestListView,
    PendingAdminMeetingRequestListView,
    RequesterAdminMeetingRescheduleView,
    StudentMeetingInboxView,
    StudentMeetingRespondView,
    TeacherMeetingActionView,
    TeacherMeetingCreateView,
    TeacherMeetingTeacherListView,
    TriggerDueMeetingRemindersView,
)

urlpatterns = [
    # Teacher/Staff -> Admin requests
    path('admin-requests/create/', AdminMeetingRequestCreateView.as_view(), name='admin-request-create'),
    path('admin-requests/admin-staff-recipients/', AdminStaffRecipientListView.as_view(), name='admin-staff-recipient-list'),
    path('admin-requests/my/', MyAdminMeetingRequestListView.as_view(), name='admin-request-my-list'),
    path('admin-requests/pending/', PendingAdminMeetingRequestListView.as_view(), name='admin-request-pending-list'),
    path('admin-requests/<int:request_id>/approve/', AdminMeetingApproveView.as_view(), name='admin-request-approve'),
    path('admin-requests/<int:request_id>/reject/', AdminMeetingRejectView.as_view(), name='admin-request-reject'),
    path('admin-requests/<int:request_id>/propose-time/', AdminMeetingProposeTimeView.as_view(), name='admin-request-propose-time'),
    path('admin-requests/<int:request_id>/request-reschedule/', RequesterAdminMeetingRescheduleView.as_view(), name='admin-request-requester-reschedule'),

    # Teacher -> Student/Parent meetings (parent actions inside student login)
    path('teacher/create/', TeacherMeetingCreateView.as_view(), name='teacher-meeting-create'),
    path('teacher/my/', TeacherMeetingTeacherListView.as_view(), name='teacher-meeting-my-list'),
    path('teacher/<int:meeting_id>/action/', TeacherMeetingActionView.as_view(), name='teacher-meeting-action'),

    # Student portal (acts as student or parent)
    path('student/inbox/', StudentMeetingInboxView.as_view(), name='student-meeting-inbox'),
    path('student/<int:meeting_id>/respond/', StudentMeetingRespondView.as_view(), name='student-meeting-respond'),

    # Reminder dispatch trigger
    path('reminders/trigger/', TriggerDueMeetingRemindersView.as_view(), name='meeting-reminders-trigger'),
]
