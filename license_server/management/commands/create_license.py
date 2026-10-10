from django.core.management.base import BaseCommand, CommandError

from license_server import core
from licensing import protocol


class Command(BaseCommand):
    help = "Nayi license key banao (customer ko bhejne ke liye). Key sirf abhi dikhti hai."

    def add_arguments(self, parser):
        parser.add_argument("--customer", required=True)
        parser.add_argument("--type", choices=protocol.TYPES, default="yearly")
        parser.add_argument("--email", default="")
        parser.add_argument("--machines", type=int, default=1, help="Kitne PC par (default 1)")
        parser.add_argument("--days", type=int, default=None, help="Monthly / yearly ke din (default 30 / 365)")
        parser.add_argument("--notes", default="")

    def handle(self, *args, **o):
        if o["machines"] < 1:
            raise CommandError("--machines kam se kam 1 hona chahiye")
        lic, key = core.create_license(customer_name=o["customer"], license_type=o["type"], customer_email=o["email"],
                                       max_machines=o["machines"], duration_days=o["days"], notes=o["notes"])
        self.stdout.write(self.style.SUCCESS(f"License created for {lic.customer_name} ({lic.get_license_type_display()})"))
        self.stdout.write(f"KEY: {key}")
        self.stdout.write("(Ye key dobara nahi dikhegi. Customer ko abhi bhej do.)")
