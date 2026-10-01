import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Delete all application data and create one new super administrator."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            required=True,
            help="Must be exactly DELETE_ALL_DATA.",
        )

    def handle(self, *args, **options):
        if options["confirm"] != "DELETE_ALL_DATA":
            raise CommandError("Reset cancelled: invalid confirmation value.")

        username = os.getenv("RESET_ADMIN_USERNAME", "").strip()
        password = os.getenv("RESET_ADMIN_PASSWORD", "")
        email = os.getenv("RESET_ADMIN_EMAIL", "").strip()

        if not username:
            raise CommandError("RESET_ADMIN_USERNAME is required.")
        if not password:
            raise CommandError("RESET_ADMIN_PASSWORD is required.")

        self.stdout.write("Deleting all database data...")
        call_command("flush", interactive=False, verbosity=options["verbosity"])

        user_model = get_user_model()
        user_model.objects.create_superuser(
            username=username,
            password=password,
            email=email,
            user_type="super_admin",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Database reset complete. Super administrator '{username}' created."
            )
        )
