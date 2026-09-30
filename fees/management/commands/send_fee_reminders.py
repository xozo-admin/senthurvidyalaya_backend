from django.core.management.base import BaseCommand
from fees.services import send_due_fee_reminders

class Command(BaseCommand):
    help = 'Send fee reminders to parents of students with pending dues'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7, 
                          help='Send reminders for fees due in next N days')
        parser.add_argument('--dry-run', action='store_true', 
                          help='Preview without sending')

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        result = send_due_fee_reminders(days=days, dry_run=dry_run)

        self.stdout.write(f"Found {result['found_count']} pending fees due in next {days} days")
        if dry_run:
            self.stdout.write("DRY RUN - No reminders sent")
            return

        self.stdout.write(self.style.SUCCESS(
            "Completed: "
            f"{result['email_sent']} email reminders sent, "
            f"{result['whatsapp_sent']} WhatsApp reminders sent, "
            f"{result['skipped_count']} skipped, "
            f"{result['error_count']} errors"
        ))
