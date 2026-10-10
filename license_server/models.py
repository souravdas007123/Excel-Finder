import re
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class License(models.Model):
    TYPE_CHOICES = [("pending", "Pending (no access yet)"), ("monthly", "Monthly"), ("yearly", "Yearly"),
                    ("lifetime", "Lifetime"), ("trial", "Free trial")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)   # key ka hash (asli key save nahi hoti)
    key_hint = models.CharField(max_length=16, editable=False, help_text="Key ke aakhri akshar, pehchaan ke liye")
    license_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="yearly")
    customer_name = models.CharField(max_length=200)
    customer_email = models.EmailField(blank=True)
    password_hash = models.CharField(max_length=128, blank=True, editable=False,
                                     help_text="Sirf account (email + password) wale customers ke liye")
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
        if self.license_type == "pending":
            return "pending"
        if self.expires_at and now > self.expires_at:
            return "expired"
        if self.first_activated_at is None and not self.password_hash:
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
    secret_hash = models.CharField(max_length=64, blank=True, editable=False,
                                   help_text="Account login par is PC ko mila secret (hash)")
    active = models.BooleanField(default=True, help_text="Band karne par ye PC license se hat jata hai (seat khali)")
    searches_total = models.PositiveIntegerField(default=0)
    scans_total = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("license", "machine_id")]
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.machine_name or self.machine_id[:8]} - {self.license}"


class Account(License):
    """Admin me 'Accounts' list: wahi License table, sirf email + password wale customers."""
    class Meta:
        proxy = True
        verbose_name = "Account"
        verbose_name_plural = "Accounts (customers)"


class AppRelease(models.Model):
    """Naya app version: yahan add karte hi customers ko app ke andar 'Update' ka option dikhta hai."""
    version = models.CharField(max_length=20, unique=True, help_text="Jaise 1.1.0 (app ke search/version.py jaisa)")
    download_url = models.URLField(max_length=500, help_text="Setup.exe ka https link (Google Drive ka 'Anyone with the link' share link chalega)")
    sha256 = models.CharField(max_length=64, blank=True, help_text=(
        "Setup.exe ka SHA-256 (build ke ant me dikhta hai). Ye bhara ho tabhi app me ek-click 'Update now' chalta hai; "
        "khali ho toh sirf 'Download' link dikhta hai."))
    notes = models.TextField(blank=True, help_text="Naya kya hai: ek line me ek baat")
    min_version = models.CharField(max_length=20, blank=True, help_text=(
        "Isse purane version par update zaruri (laal banner). Khali = zaruri nahi"))
    published = models.BooleanField(default=True, help_text="Untick karne par customers ko nahi dikhta")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Version {self.version}"

    def clean(self):
        errors = {}
        self.version = (self.version or "").strip()
        if not re.fullmatch(r"\d+(\.\d+){1,3}", self.version):
            errors["version"] = "Version must look like 1.1.0"
        self.min_version = (self.min_version or "").strip()
        if self.min_version and not re.fullmatch(r"\d+(\.\d+){1,3}", self.min_version):
            errors["min_version"] = "Must look like 1.0.0 (or leave empty)"
        if not (self.download_url or "").startswith("https://"):
            errors["download_url"] = "The link must start with https://"
        self.sha256 = (self.sha256 or "").strip().lower()
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            errors["sha256"] = "SHA-256 must be 64 characters (0-9, a-f), or leave empty"
        if errors:
            raise ValidationError(errors)
