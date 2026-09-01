from django.contrib import admin
from django.urls import include, path
from gst_tally.views import (GSTLookupBulkView, GSTLookupStatusView, GSTLookupView,
                             SandboxAuthenticateView, SandboxRequestOTPView,
                             SandboxStatusView, SandboxVerifyOTPView)

urlpatterns = [path("admin/", admin.site.urls), path("api/auth/", include("gst_tally.auth_urls")),
               path("api/gst/lookup/status/", GSTLookupStatusView.as_view()),
               path("api/gst/lookup/bulk/", GSTLookupBulkView.as_view()),
               path("api/gst/lookup/<str:gstin>/", GSTLookupView.as_view()),
               path("api/gst/sandbox/status/", SandboxStatusView.as_view()),
               path("api/gst/sandbox/authenticate/", SandboxAuthenticateView.as_view()),
               path("api/gst/sandbox/request-otp/", SandboxRequestOTPView.as_view()),
               path("api/gst/sandbox/verify-otp/", SandboxVerifyOTPView.as_view()),
               path("api/gst-tally/", include("gst_tally.urls"))]
