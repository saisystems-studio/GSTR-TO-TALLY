from django.urls import path

from . import views as v

urlpatterns = [
    path("config/", v.ConfigView.as_view()),
    path("tally/detect/", v.DetectSerialView.as_view()),
    path("connector/pair/", v.ConnectorPairView.as_view()),
    path("connector/discovery/", v.ConnectorDiscoveryView.as_view()),
    path("register/", v.RegisterView.as_view()),
    path("login/", v.LoginView.as_view()),
    path("logout/", v.LogoutView.as_view()),
    path("password/change/", v.ChangePasswordView.as_view()),
    path("password/forgot/", v.ForgotPasswordView.as_view()),
    path("password/reset/", v.ResetPasswordView.as_view()),
    path("password/verify-otp/", v.VerifyOtpView.as_view()),
    path("credentials/resend/", v.ResendCredentialsView.as_view()),
    path("activate/request-code/", v.ActivateRequestCodeView.as_view()),
    path("activate/verify/", v.ActivateVerifyView.as_view()),
    path("activate/confirm/", v.ActivateConfirmView.as_view()),
    path("session/", v.SessionView.as_view()),
    path("token/refresh/", v.RefreshNexusTokenView.as_view()),
    path("license/", v.LicenseView.as_view()),
    path("tally-info/sync/", v.TallyInfoSyncView.as_view()),
    path("usage/open/", v.UsageOpenView.as_view()),
]
