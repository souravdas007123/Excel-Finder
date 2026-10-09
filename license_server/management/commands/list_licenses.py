from django.core.management.base import BaseCommand
from django.utils import timezone

from license_server.models import License


class Command(BaseCommand):
    help = "Saari licenses, status, kitne PC aur aakhri baar kab dikha."

    def handle(self, *args, **o):
        rows = [("CUSTOMER", "TYPE", "KEY", "STATUS", "EXPIRES", "PCs", "LAST SEEN")]
        for lic in License.objects.prefetch_related("activations"):
            acts = list(lic.activations.all())
            seen = max((a.last_seen for a in acts), default=None)
            rows.append((lic.customer_name[:28], lic.license_type, lic.key_hint, lic.state(),
                         f"{lic.expires_at:%d %b %Y}" if lic.expires_at else "-",
                         f"{sum(a.active for a in acts)}/{lic.max_machines}",
                         timezone.localtime(seen).strftime("%d %b %H:%M") if seen else "-"))
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for r in rows:
            self.stdout.write("  ".join(c.ljust(w) for c, w in zip(r, widths)))
