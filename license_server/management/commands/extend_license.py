from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from license_server import core


class Command(BaseCommand):
    help = "Renewal: license ko din jodo (bacha hua time khoye bina)."

    def add_arguments(self, parser):
        parser.add_argument("license", help="License id ya key ke aakhri 5 akshar (list_licenses me dikhte hain)")
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **o):
        try:
            lic = core.get_license(o["license"])
        except LookupError as exc:
            raise CommandError(str(exc))
        if lic.license_type == "lifetime":
            raise CommandError("Lifetime license ko badhane ki zarurat nahi.")
        core.extend(lic, o["days"])
        self.stdout.write(self.style.SUCCESS(f"{lic.customer_name}: now valid until {timezone.localtime(lic.expires_at):%d %b %Y}"
                                             if lic.expires_at else f"{lic.customer_name}: +{o['days']} days will apply on first activation"))
