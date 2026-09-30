from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from fees.models import FeePayment


class Command(BaseCommand):
    help = 'Expire stale INITIATED fee payments by marking them FAILED'

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes',
            type=int,
            default=30,
            help='Expire INITIATED payments older than this many minutes (default: 30)'
        )

    def handle(self, *args, **options):
        minutes = options['minutes']
        expiry_time = timezone.now() - timedelta(minutes=minutes)

        updated = FeePayment.objects.filter(
            payment_status='INITIATED',
            created_at__lt=expiry_time,
        ).update(
            payment_status='FAILED',
            remarks='Payment expired',
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {updated} payment(s) older than {minutes} minute(s)."
            )
        )
