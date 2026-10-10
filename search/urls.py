from django.contrib import admin
from django.urls import path,include
from django.views.generic import RedirectView
from . import setup_views

admin.site.site_header = 'Excel Finder'       # upar ki bar me 'Django administration' ki jagah
admin.site.site_title = 'Excel Finder'
admin.site.index_title = 'Welcome'

urlpatterns = [
    path('', RedirectView.as_view(url='/app/')),
    path('app/', include('webui.urls')),
    path('setup/', setup_views.setup, name='setup'),
    path('admin/', admin.site.urls),
]