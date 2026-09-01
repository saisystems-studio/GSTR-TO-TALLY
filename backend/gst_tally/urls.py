from django.urls import path
from .views import (BatchCompanyView, BatchDetailView, BatchListView, BatchPartiesView, BatchPartyDetailView, ImportView,
                    SourcePreviewView, TallyConnectionView, TallyDiagnosticsView, TallyImportView, TallyLicenseView,
                    TallyMastersView, TallyVoucherCorrectionView, TallyVoucherPreviewView)

urlpatterns = [
    path("preview/", SourcePreviewView.as_view()), path("import/", ImportView.as_view()),
    path("batches/", BatchListView.as_view()), path("batches/<int:pk>/", BatchDetailView.as_view()),
    path("import-batches/<int:pk>/fetch-parties/", BatchPartiesView.as_view()),
    path("import-batches/<int:pk>/parties/", BatchPartiesView.as_view()),
    path("import-batches/<int:pk>/parties/<str:gstin>/", BatchPartyDetailView.as_view()),
    path("import-batches/<int:pk>/company/", BatchCompanyView.as_view()),
    path("import-batches/<int:pk>/tally-masters/prepare/", TallyMastersView.as_view()),
    path("import-batches/<int:pk>/tally-license/verify/", TallyLicenseView.as_view()),
    path("tally/connection/", TallyConnectionView.as_view()),
    path("tally/diagnostics/", TallyDiagnosticsView.as_view()),
    path("import-batches/<int:pk>/tally-vouchers/preview/", TallyVoucherPreviewView.as_view()),
    path("import-batches/<int:pk>/tally-vouchers/correct/", TallyVoucherCorrectionView.as_view()),
    path("import-batches/<int:pk>/tally-import/", TallyImportView.as_view()),
]
