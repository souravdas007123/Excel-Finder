from django.contrib import admin
from django.urls import path,include
from django.views.generic import RedirectView
from fileindex import views 
from . import setup_views, update_views

admin.site.site_header = 'Excel Finder'       # upar ki bar me 'Django administration' ki jagah
admin.site.site_title = 'Excel Finder'
admin.site.index_title = 'Welcome'

urlpatterns = [
    path('', RedirectView.as_view(url='/app/')),
    path('app/', include('webui.urls')),
    path('setup/', setup_views.setup, name='setup'),
    path('admin/update/check/', update_views.check, name='update_check'),
    path('admin/update/install/', update_views.install, name='update_install'),
    path('admin/update/status/', update_views.status, name='update_status'),
    path('admin/update/dismiss/', update_views.dismiss, name='update_dismiss'),
    path('admin/start-scan-api/', views.start_scan_api, name='start_scan'),
    path('admin/check-task/<int:task_id>/', views.check_scan_status, name='check_scan'),
    
    # Ye naya Bulk Search ka URL add kiya hai
    path('admin/bulk-search-api/', views.bulk_search_api, name='bulk_search'),
    path('admin/row-detail-api/', views.row_detail_api, name='row_detail'),
    path('admin/browse-folders-api/', views.browse_folders_api, name='browse_folders'),
    path('admin/extract-numbers-api/', views.extract_numbers_api, name='extract_numbers'),
    path('admin/export-excel-api/', views.export_excel_api, name='export_excel'),
    path('admin/clear-index-api/', views.clear_index_api, name='clear_index'),
    path('admin/file-report-api/', views.file_report_api, name='file_report'),
    path('admin/file-locations-api/', views.file_locations_api, name='file_locations'),
    path('admin/stop-scan-api/', views.stop_scan_api, name='stop_scan'),
    path('admin/', admin.site.urls),
]