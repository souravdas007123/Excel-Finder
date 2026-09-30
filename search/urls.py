from django.contrib import admin
from django.urls import path
from fileindex import views 

urlpatterns = [
    path('admin/start-scan-api/', views.start_scan_api, name='start_scan'),
    path('admin/check-task/<int:task_id>/', views.check_scan_status, name='check_scan'),
    
    # Ye naya Bulk Search ka URL add kiya hai
    path('admin/bulk-search-api/', views.bulk_search_api, name='bulk_search'),
    path('admin/row-detail-api/', views.row_detail_api, name='row_detail'),
    path('admin/', admin.site.urls),
]