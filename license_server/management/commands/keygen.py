from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from licensing import protocol


class Command(BaseCommand):
    help = "Signing keypair banao (sirf ek baar). Private key server par, public key app me jati hai."

    def add_arguments(self, parser):
        parser.add_argument("--write", action="store_true",
                            help="Private key ko license_server/private_key.txt me save karo (agar pehle se nahi hai)")

    def handle(self, *args, **opts):
        target = Path(__file__).resolve().parents[2] / "private_key.txt"
        if opts["write"] and target.exists():
            raise CommandError(f"{target} pehle se hai. Purani key badalne se SAARE customers ke token invalid ho jayenge. "
                               "Sach me badalna ho toh pehle file hata do.")
        private, public = protocol.generate_keypair()
        if opts["write"]:
            target.write_text(private + "\n")
            self.stdout.write(self.style.SUCCESS(f"Private key saved: {target}  (isko backup me rakho, kisi ko mat do)"))
        else:
            self.stdout.write(f"PRIVATE (server par secret rakho):\n{private}\n")
        self.stdout.write(f"PUBLIC (app me jayegi - installer banate waqt):\n{public}")
