import os
import re
import threading
import pandas as pd
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from .models import FileIndex, ScanTask

def scan_drive_thread(drive_path, task_id):
    print(f"\n---- SCANNING STARTING FOR PATH: {drive_path} ----")
    
    # Counters initialize karein
    folder_count = 0
    file_count = 0
    
    try:
        task = ScanTask.objects.get(id=task_id)
        
        if not drive_path.endswith('\\') and not drive_path.endswith('/'):
            drive_path = drive_path + '\\'
            
        for root, dirs, files in os.walk(drive_path):
            folder_count += 1 # Har naye folder par count badhayein
            
            for file in files:
                if file.endswith('.xlsx') or file.endswith('.xls'):
                    file_count += 1 # Har Excel file par count badhayein
                    full_path = os.path.join(root, file)
                    
                    try:
                        df = pd.read_excel(full_path)
                        text_data = df.to_string(index=False) 
                        FileIndex.objects.update_or_create(
                            file_path=full_path,
                            defaults={'file_name': file, 'extracted_data': text_data}
                        )
                    except Exception as e:
                        print(f"❌ Error reading {file}: {e}")
        
        # Loop khatam hone ke baad counts save karein
        task.folders_scanned = folder_count
        task.files_indexed = file_count
        task.status = 'Completed'
        task.save()
        print(f"---- DONE! Folders: {folder_count}, Files: {file_count} ----\n")
        
    except Exception as e:
        print(f"System Error: {e}")
        task = ScanTask.objects.get(id=task_id)
        task.status = 'Error'
        task.save()

# (start_scan_api same rahega, usme koi change nahi)
@staff_member_required
def start_scan_api(request):
    if request.method == "POST":
        drive = request.POST.get('drive_path')
        task = ScanTask.objects.create(status='Running')
        thread = threading.Thread(target=scan_drive_thread, args=(drive, task.id))
        thread.daemon = True 
        thread.start()
        return JsonResponse({'task_id': task.id, 'status': 'started'})
    return JsonResponse({'error': 'Invalid request'}, status=400)

# Yahan response mein counts add kiye hain
@staff_member_required
def check_scan_status(request, task_id):
    try:
        task = ScanTask.objects.get(id=task_id)
        return JsonResponse({
            'status': task.status,
            'folders_scanned': task.folders_scanned,
            'files_indexed': task.files_indexed
        })
    except ScanTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)

@staff_member_required
def bulk_search_api(request):
    if request.method == "POST":
        numbers_raw = request.POST.get('numbers', '')
        
        # Numbers ko comma ya new line se alag (split) karein
        # re module numbers ko clean list me convert karega
        numbers_list = [n.strip() for n in re.split(r'[,\n]+', numbers_raw) if n.strip()]
        
        # Ek limit laga dete hain taaki server hang na ho (Max 500 numbers at a time)
        numbers_list = list(set(numbers_list))[:500] 
        
        results = []
        for num in numbers_list:
            # Database mein search karein ki ye number kis-kis file ke text me hai
            # flat=True se hume sirf file names ki list milegi
            matched_files = list(FileIndex.objects.filter(extracted_data__icontains=num).values_list('file_name', flat=True))
            
            results.append({
                'number': num,
                'files': matched_files,
                'found': len(matched_files) > 0
            })
            
        return JsonResponse({'status': 'success', 'results': results})
    return JsonResponse({'error': 'Invalid request'}, status=400)    