import os
import pandas as pd
from celery import shared_task
from .models import FileIndex

@shared_task
def scan_drive_task(drive_path):
    # Drive/Folder scan logic
    for root, dirs, files in os.walk(drive_path):
        for file in files:
            if file.endswith('.xlsx') or file.endswith('.xls'):
                full_path = os.path.join(root, file)
                
                try:
                    # Pandas se fast Excel read karein
                    df = pd.read_excel(full_path)
                    
                    # Saare data ko ek string mein convert karein taaki search easy ho
                    # (Aap chahein toh sirf phone number wale columns target kar sakte hain)
                    text_data = df.to_string(index=False) 
                    
                    # Database mein update ya create karein
                    FileIndex.objects.update_or_create(
                        file_path=full_path,
                        defaults={
                            'file_name': file,
                            'extracted_data': text_data
                        }
                    )
                except Exception as e:
                    print(f"Error reading {full_path}: {e}")
                    
    return "Scanning Complete"