from django.urls import path

from apps.accounts import views
from apps.training.views import PreferencesView

urlpatterns = [
    path("me/", views.MeView.as_view(), name="users-me"),
    path("me/password/", views.ChangePasswordView.as_view(), name="users-me-password"),
    path("me/preferences/", PreferencesView.as_view(), name="users-me-preferences"),
]
