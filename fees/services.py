import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from notifications.whatsapp import send_whatsapp_template

from .models import FeeReminder, StudentFee


logger = logging.getLogger(__name__)


def get_due_fee_queryset(days=7):
    today = timezone.localdate()
    due_date_threshold = today + timedelta(days=int(days or 7))

    return StudentFee.objects.filter(
        fee_definition__due_date__lte=due_date_threshold,
        fee_definition__due_date__gte=today,
        fee_definition__is_active=True,
    ).exclude(status='PAID').select_related(
        'student',
        'student__section',
        'student__section__standard',
        'fee_definition',
    )


def send_due_fee_reminders(days=7, dry_run=False, include_email=True, include_whatsapp=True):
    today = timezone.localdate()
    pending_fees = get_due_fee_queryset(days=days)

    summary = {
        'found_count': pending_fees.count(),
        'email_sent': 0,
        'whatsapp_sent': 0,
        'skipped_count': 0,
        'error_count': 0,
        'dry_run': bool(dry_run),
    }

    if dry_run:
        return summary

    for fee in pending_fees:
        student = fee.student
        parent_email = getattr(student, 'parent_email', None)
        parent_phone = getattr(student, 'father_phone', '') or getattr(student, 'mother_phone', '')

        if not parent_email and not parent_phone:
            summary['skipped_count'] += 1
            continue

        already_sent_today = FeeReminder.objects.filter(
            student_fee=fee,
            reminder_date__date=today,
        )

        subject = f"Fee Reminder: {fee.fee_definition.fee_type} Fee Due"
        class_name = ''
        if getattr(student, 'section', None) and getattr(student.section, 'standard', None):
            class_name = f"{student.section.standard.name}-{student.section.name}"
        elif getattr(student, 'standard', None):
            class_name = student.standard.name

        message = (
            f"Fee reminder: {fee.fee_definition.fee_type} fee for "
            f"{student.student_name}"
            f"{f' ({class_name})' if class_name else ''} is due on "
            f"{fee.fee_definition.due_date}. Balance due: Rs.{fee.due_amount}."
        )

        if include_email and parent_email and not already_sent_today.filter(reminder_type='EMAIL').exists():
            try:
                email_body = (
                    f"Dear Parent,\n\n"
                    f"This is a reminder that the {fee.fee_definition.fee_type} fee of Rs.{fee.effective_total} "
                    f"for your child {student.student_name} is due on {fee.fee_definition.due_date}.\n\n"
                    f"Balance Due: Rs.{fee.due_amount}\n\n"
                    "Please make the payment before the due date.\n\n"
                    "Thank you,\nSchool Administration"
                )
                send_mail(
                    subject,
                    email_body,
                    settings.DEFAULT_FROM_EMAIL,
                    [parent_email],
                    fail_silently=False,
                )
                FeeReminder.objects.create(
                    student_fee=fee,
                    reminder_type='EMAIL',
                    sent_to=parent_email,
                    status='SENT',
                )
                summary['email_sent'] += 1
            except Exception as exc:
                summary['error_count'] += 1
                logger.exception("Email fee reminder failed for %s: %s", student.student_id, exc)

        if include_whatsapp and parent_phone and not already_sent_today.filter(reminder_type='WHATSAPP').exists():
            whatsapp_ok, whatsapp_response = send_whatsapp_template(
                parent_phone,
                message,
                alert_type='fees',
            )

            whatsapp_error = str(whatsapp_response.get('error', ''))
            if (
                not whatsapp_ok
                and (
                    whatsapp_error.startswith('WhatsApp alert disabled:')
                    or whatsapp_error == 'WhatsApp alerts are not configured.'
                )
            ):
                summary['skipped_count'] += 1
                continue

            FeeReminder.objects.create(
                student_fee=fee,
                reminder_type='WHATSAPP',
                sent_to=parent_phone,
                status='SENT' if whatsapp_ok else 'FAILED',
                response_data=whatsapp_response,
            )

            if whatsapp_ok:
                summary['whatsapp_sent'] += 1
            else:
                summary['error_count'] += 1
                logger.warning("WhatsApp fee reminder failed for %s: %s", student.student_id, whatsapp_response)

    return summary
