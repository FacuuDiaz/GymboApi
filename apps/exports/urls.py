from django.urls import path

from .views import ExportView

urlpatterns = [path("exports/", ExportView.as_view(), name="exports")]
