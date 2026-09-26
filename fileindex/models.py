from django.db import models

class FileIndex(models.Model):
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1000, unique=True)
    extracted_data = models.TextField(help_text="All extracted text/numbers from the file")
    last_scanned = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.file_name

class ScanTask(models.Model):
    status = models.CharField(max_length=50, default='Running')
    # Ye 2 nayi lines add ki hain:
    folders_scanned = models.IntegerField(default=0) 
    files_indexed = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)