from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("logout-all/", views.LogoutAllView.as_view(), name="auth-logout-all"),
    path("google/", views.GoogleLoginView.as_view(), name="auth-google"),
    path("google/link/", views.GoogleLinkView.as_view(), name="auth-google-link"),
    path("google/unlink/", views.GoogleUnlinkView.as_view(), name="auth-google-unlink"),
]
