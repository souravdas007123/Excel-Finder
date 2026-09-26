from django.contrib import admin
from .models import FileIndex, ScanTask

@admin.register(FileIndex)
class FileIndexAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'file_path', 'last_scanned')
    # Ye line sabse important hai fast search ke liye
    search_fields = ('extracted_data', 'file_name') 
    
    change_list_template = "admin/file_index_changelist.html"

# Task status dekhne ke liye (Optional, par debugging me kaam aayega)
@admin.register(ScanTask)
class ScanTaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'folders_scanned','files_indexed','created_at')