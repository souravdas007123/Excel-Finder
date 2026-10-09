from django.db import models


class FileIndex(models.Model):
    """Ek Excel file ka record. Size + mtime se pata chalta hai file badli ya nahi."""
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1000, unique=True)
    file_size = models.BigIntegerField(default=0)
    file_mtime = models.FloatField(default=0)
    index_version = models.PositiveSmallIntegerField(default=1)  # scanner version; badalne par files auto re-index hoti hain
    last_scanned = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.file_name


class NumberIndex(models.Model):
    """Har number ki alag row -> search indexed aur exact match hota hai."""
    file = models.ForeignKey(FileIndex, on_delete=models.CASCADE, related_name="numbers")
    number = models.CharField(max_length=32, db_index=True)
    match_key = models.CharField(max_length=32, db_index=True, default="")   # aakhri 10 digits (country code ignore)
    sheet = models.CharField(max_length=100, blank=True)
    row = models.PositiveIntegerField()
    col = models.PositiveSmallIntegerField(default=0)  # 1 = column A

    def __str__(self):
        return f"{self.number} ({self.file_id}, {self.sheet}, row {self.row})"


class ScanTask(models.Model):
    status = models.CharField(max_length=50, default="Running")
    folders_scanned = models.IntegerField(default=0)
    files_indexed = models.IntegerField(default=0)
    files_skipped = models.IntegerField(default=0)   # unchanged files
    files_failed = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)  # current file / error text
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Scan #{self.pk} ({self.status})"


class ScanFailure(models.Model):
    """Scan me jo file padhi nahi gayi: kaun si file aur kyun (Scan History page par dikhta hai)."""
    task = models.ForeignKey(ScanTask, on_delete=models.CASCADE, related_name="failures")
    file_path = models.CharField(max_length=1000)
    reason = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.file_path


class BulkSearch(FileIndex):
    """Sirf admin sidebar me 'Bulk Number Search' ka link dikhane ke liye (proxy = koi nayi table nahi banti)."""
    class Meta:
        proxy = True
        verbose_name = "Bulk Number Search"
        verbose_name_plural = "Bulk Number Search"
