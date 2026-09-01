from django.urls import path

from .auth_views import DeviceVerifyView, LoginView, LogoutView, MeView, RefreshView, RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view()),
    path("login/", LoginView.as_view()),
    path("refresh/", RefreshView.as_view()),
    path("logout/", LogoutView.as_view()),
    path("me/", MeView.as_view()),
    path("device/verify/", DeviceVerifyView.as_view()),
]
