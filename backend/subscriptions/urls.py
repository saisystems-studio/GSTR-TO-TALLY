from django.urls import path

from .views import (AdminActivateView, AdminReactivateView, AdminRenewView, AdminSubscriptionDetailView,
                     AdminSubscriptionListView, AdminSuspendView, MySubscriptionView)

urlpatterns = [
    path("me/", MySubscriptionView.as_view()),
    path("admin/", AdminSubscriptionListView.as_view()),
    path("admin/<int:user_id>/", AdminSubscriptionDetailView.as_view()),
    path("admin/<int:user_id>/activate/", AdminActivateView.as_view()),
    path("admin/<int:user_id>/renew/", AdminRenewView.as_view()),
    path("admin/<int:user_id>/suspend/", AdminSuspendView.as_view()),
    path("admin/<int:user_id>/reactivate/", AdminReactivateView.as_view()),
]
