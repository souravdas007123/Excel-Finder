from django.contrib import admin
from django.urls import path

from . import views

admin.site.site_header = "Excel Finder - License Server"
admin.site.site_title = "License Server"
admin.site.index_title = "Licenses and activations"

urlpatterns = [
    path("api/v1/ping", views.ping),
    path("api/v1/<str:action>", views.api),
    path("", admin.site.urls),
]
