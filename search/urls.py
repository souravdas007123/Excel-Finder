from django.urls import include, path
from django.views.generic import RedirectView

from . import setup_views

urlpatterns = [
    path('', RedirectView.as_view(url='/app/')),
    path('app/', include('webui.urls')),
    path('setup/', setup_views.setup, name='setup'),
]
