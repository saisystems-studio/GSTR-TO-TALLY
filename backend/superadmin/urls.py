from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path("auth/login/", views.SuperAdminLoginView.as_view()),
    path("auth/refresh/", views.SuperAdminRefreshView.as_view()),
    path("auth/logout/", views.SuperAdminLogoutView.as_view()),
    path("auth/me/", views.SuperAdminMeView.as_view()),
    path("auth/change-password/", views.SuperAdminChangePasswordView.as_view()),
    path("auth/forgot-password/", views.SuperAdminForgotPasswordView.as_view()),
    path("auth/reset-password/", views.SuperAdminResetPasswordView.as_view()),

    # Dashboard
    path("dashboard/", views.SuperAdminDashboardView.as_view()),

    # Customers
    path("customers/", views.CustomerListView.as_view()),
    path("customers/<int:user_id>/", views.CustomerDetailView.as_view()),
    path("customers/<int:user_id>/suspend/", views.CustomerSuspendView.as_view()),
    path("customers/<int:user_id>/reactivate/", views.CustomerReactivateView.as_view()),
    path("customers/<int:user_id>/company-limit/", views.CustomerCompanyLimitView.as_view()),
    path("customers/<int:user_id>/notes/", views.CustomerNotesView.as_view()),
    path("customers/<int:user_id>/companies/", views.CustomerCompaniesView.as_view()),

    # Companies
    path("companies/", views.CompanyListView.as_view()),
    path("companies/<int:company_id>/", views.CompanyDetailView.as_view()),
    path("companies/<int:company_id>/device/<int:device_id>/reset/", views.CompanyDeviceResetView.as_view()),

    # Plans
    path("plans/", views.PlanListView.as_view()),
    path("plans/<int:plan_id>/", views.PlanDetailView.as_view()),

    # Subscriptions
    path("subscriptions/", views.SubscriptionListView.as_view()),
    path("subscriptions/<int:user_id>/renew/", views.SubscriptionRenewView.as_view()),
    path("subscriptions/<int:user_id>/change-plan/", views.SubscriptionChangePlanView.as_view()),

    # Payments
    path("payments/", views.PaymentListView.as_view()),
    path("payments/<int:payment_id>/", views.PaymentDetailView.as_view()),

    # Usage
    path("usage/", views.UsageView.as_view()),

    # Support
    path("support/", views.SupportTicketListView.as_view()),
    path("support/<int:ticket_id>/", views.SupportTicketDetailView.as_view()),

    # Audit logs
    path("audit-logs/", views.AuditLogListView.as_view()),

    # Product licenses
    path("product-licenses/", views.ProductLicenseListView.as_view()),
    path("product-licenses/<int:license_id>/", views.ProductLicenseDetailView.as_view()),
    path("product-licenses/<int:license_id>/extend/", views.ProductLicenseExtendView.as_view()),
    path("product-licenses/<int:license_id>/change-tally-serial/", views.ProductLicenseChangeTallySerialView.as_view()),
    path("product-licenses/<int:license_id>/devices/<int:device_id>/revoke/", views.ProductLicenseDeviceRevokeView.as_view()),
    path("product-licenses/<int:license_id>/<str:action>/", views.ProductLicenseStatusView.as_view()),
    path("device-requests/", views.DeviceActivationRequestListView.as_view()),
    path("device-requests/<int:request_id>/<str:action>/", views.DeviceActivationRequestActionView.as_view()),

    # Settings
    path("settings/", views.SuperAdminSettingsView.as_view()),
    path("sandbox-configuration/", views.SandboxConfigurationView.as_view()),

    # Reports
    path("reports/<str:kind>/export/", views.ReportExportView.as_view()),
]
