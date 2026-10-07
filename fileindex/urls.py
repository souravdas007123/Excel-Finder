from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_view, name='home'), # Homepage ke liye
    path('search/', views.search_view, name='search'),
]