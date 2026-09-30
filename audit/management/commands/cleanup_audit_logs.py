from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditLog


class Command(BaseCommand):
    help = 'Delete old audit logs based on retention days (default: 730 days)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=730,
            help='Retention period in days. Logs older than this will be deleted (default: 730).'
        )

    def handle(self, *args, **options):
        retention_days = options['days']
        cutoff = timezone.now() - timedelta(days=retention_days)

        deleted_count, _ = AuditLog.objects.filter(timestamp__lt=cutoff).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count} audit log row(s) older than {retention_days} day(s)."
            )
        )
