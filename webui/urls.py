from django.urls import path

from . import license_views, scan_views, search_views, views

app_name = "webui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("forgot/", views.forgot_password, name="forgot"),
    path("dashboard/stats/", views.dashboard_stats, name="dashboard_stats"),

    path("scan/", scan_views.scan_page, name="scan"),
    path("scan/start/", scan_views.scan_start, name="scan_start"),
    path("scan/status/", scan_views.scan_status, name="scan_status"),
    path("scan/stop/", scan_views.scan_stop, name="scan_stop"),
    path("scan/browse/", scan_views.scan_browse, name="scan_browse"),
    path("scan/clear/", scan_views.scan_clear, name="scan_clear"),

    path("search/", search_views.search_page, name="search"),
    path("search/run/", search_views.search_run, name="search_run"),
    path("search/extract/", search_views.search_extract, name="search_extract"),
    path("search/row/", search_views.search_row, name="search_row"),
    path("search/export/", search_views.search_export, name="search_export"),
    path("search/report/", search_views.search_report, name="search_report"),
    path("search/<str:token>/files/<int:file_id>/", search_views.search_file_details, name="search_file"),
    path("search/<str:token>/<str:tab>/", search_views.search_tab, name="search_tab"),

    path("files/", views.files, name="files"),
    path("history/", views.history, name="history"),
    path("history/<int:task_id>/", views.history_detail, name="history_detail"),

    path("license/", license_views.license_page, name="license"),
    path("license/check/", license_views.license_check, name="license_check"),
    path("license/activate/", license_views.license_activate, name="license_activate"),
    path("license/deactivate/", license_views.license_deactivate, name="license_deactivate"),

    path("updates/check/", license_views.update_check_now, name="update_check"),
    path("updates/dismiss/", license_views.update_dismiss, name="update_dismiss"),
    path("updates/install/", license_views.update_install, name="update_install"),
    path("updates/status/", license_views.update_status, name="update_status"),
]
