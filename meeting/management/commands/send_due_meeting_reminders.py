from django.core.management.base import BaseCommand
from django.utils import timezone

from meeting.models import MeetingReminder
from notifications.models import Notification
from notifications.whatsapp import send_whatsapp_to_user


class Command(BaseCommand):
    help = 'Send due in-app meeting reminders and mark status.'

    def handle(self, *args, **options):
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

                Notification.objects.create(
                    recipient=reminder.recipient,
                    sender=None,
                    title=title,
                    message=message,
                    notification_type='Meeting Reminder',
                )
                whatsapp_ok, whatsapp_response = send_whatsapp_to_user(
                    reminder.recipient,
                    f"{title}: {message}",
                    notification_type='Meeting Reminder',
                )
                whatsapp_error = str(whatsapp_response.get('error', ''))

                reminder.status = MeetingReminder.STATUS_SENT
                if (
                    not whatsapp_ok
                    and whatsapp_error != 'WhatsApp alerts are not configured.'
                    and not whatsapp_error.startswith('WhatsApp alert disabled:')
                ):
                    reminder.error_message = str(whatsapp_response)
                reminder.sent_at = timezone.now()
                reminder.save(update_fields=['status', 'error_message', 'sent_at', 'updated_at'])
                sent_count += 1
            except Exception as exc:
                reminder.status = MeetingReminder.STATUS_FAILED
                reminder.error_message = str(exc)
                reminder.sent_at = timezone.now()
                reminder.save(update_fields=['status', 'error_message', 'sent_at', 'updated_at'])
                failed_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Processed={due.count()} Sent={sent_count} Failed={failed_count}"
        ))
