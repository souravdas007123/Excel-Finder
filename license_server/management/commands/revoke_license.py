from django.core.management.base import BaseCommand, CommandError

from license_server import core


class Command(BaseCommand):
    help = "License band karo (ya --undo se wapas chalu). App agle check par block ho jata hai."

    def add_arguments(self, parser):
        parser.add_argument("license", help="License id ya key ke aakhri 5 akshar")
        parser.add_argument("--reason", default="")
        parser.add_argument("--undo", action="store_true")

    def handle(self, *args, **o):
        try:
            lic = core.get_license(o["license"])
        except LookupError as exc:
            raise CommandError(str(exc))
        lic.revoked = not o["undo"]
        lic.revoked_reason = "" if o["undo"] else (o["reason"] or "This license has been disabled. Please contact support.")
        lic.save()
        self.stdout.write(self.style.SUCCESS(f"{lic.customer_name}: {'restored' if o['undo'] else 'REVOKED'}"))
