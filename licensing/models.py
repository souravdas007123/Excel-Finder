from django.db import models


class LicenseState(models.Model):
    """Is PC par license ki halat (sirf ek row). Token server ne sign kiya hota hai, isliye badla nahi ja sakta."""
    license_key = models.CharField(max_length=40, blank=True)
    account_name = models.CharField(max_length=200, blank=True)        # customer ne app me jo naam / email diya
    account_email = models.CharField(max_length=254, blank=True)
    token = models.TextField(blank=True)
    blocked_code = models.CharField(max_length=20, blank=True)        # server ne roka: revoked / expired / not_activated
    blocked_message = models.CharField(max_length=300, blank=True)
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(default=True)
    last_error = models.CharField(max_length=300, blank=True)
    max_seen_time = models.DateTimeField(null=True, blank=True)       # clock peeche karne se bachne ke liye
    searches_total = models.PositiveIntegerField(default=0)           # sirf ginti, koi data nahi
    scans_total = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "License"
        verbose_name_plural = "License"

    def __str__(self):
        return "License"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
