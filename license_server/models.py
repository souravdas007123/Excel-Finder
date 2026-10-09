import uuid

from django.db import models
from django.utils import timezone


class License(models.Model):
    TYPE_CHOICES = [("yearly", "Yearly"), ("lifetime", "Lifetime"), ("trial", "Free trial")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)   # key ka hash (asli key save nahi hoti)
    key_hint = models.CharField(max_length=16, editable=False, help_text="Key ke aakhri akshar, pehchaan ke liye")
    license_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="yearly")
    customer_name = models.CharField(max_length=200)
    customer_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    max_machines = models.PositiveSmallIntegerField(default=1, help_text="Kitne PC par chal sakta hai")
    duration_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Pehli activation se kitne din. Yearly: 365 (khali chhodo toh apne aap). Lifetime: khali.")
    offline_grace_days = models.PositiveSmallIntegerField(
        default=14, help_text="Server se bina check kiye app itne din chalega")
    created_at = models.DateTimeField(auto_now_add=True)
    first_activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True,
                                      help_text="Khali = kabhi expire nahi (lifetime) ya abhi activate nahi hua")
    revoked = models.BooleanField(default=False, help_text="Tick karte hi app ka license band ho jata hai (agle check par)")
    revoked_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer_name} ({self.get_license_type_display()}, {self.key_hint})"

    def state(self, now=None):
        now = now or timezone.now()
        if self.revoked:
            return "revoked"
        if self.expires_at and now > self.expires_at:
            return "expired"
        if self.first_activated_at is None:
            return "unused"
        return "active"


class Activation(models.Model):
    """Ek PC par ek license ki activation + us PC ka (data-free) usage."""
    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="activations")
    machine_id = models.CharField(max_length=64)
    machine_name = models.CharField(max_length=200, blank=True)
    app_version = models.CharField(max_length=40, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    active = models.BooleanField(default=True, help_text="Band karne par ye PC license se hat jata hai (seat khali)")
    searches_total = models.PositiveIntegerField(default=0)
    scans_total = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("license", "machine_id")]
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.machine_name or self.machine_id[:8]} - {self.license}"
