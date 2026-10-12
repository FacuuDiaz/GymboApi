from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("me/", views.MeView.as_view(), name="users-me"),
    path("me/password/", views.ChangePasswordView.as_view(), name="users-me-password"),
]
