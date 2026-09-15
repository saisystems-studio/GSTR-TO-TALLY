from rest_framework import serializers
from .models import GSTCompanyImportSummary, GSTImportBatch, GSTInvoice
from .tally.return_mapping import get_tally_mapping

class GSTInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTInvoice
        exclude = ["source_type", "import_batch", "dedup_fingerprint"]


class GSTCompanyImportSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTCompanyImportSummary
        fields = [
            "id", "company_name", "company_gstin", "return_type", "transaction_type",
            "source_file_name", "source_format", "total_source_count",
            "successful_voucher_count", "failed_record_count", "needs_attention_count",
            "skipped_duplicate_count", "pending_record_count", "first_uploaded_at",
            "last_uploaded_at", "first_tally_import_at", "last_tally_import_at",
            "last_retry_at", "import_status", "created_at", "updated_at",
        ]

class GSTImportBatchSerializer(serializers.ModelSerializer):
    invoices = GSTInvoiceSerializer(many=True, read_only=True)
    tally_mapping = serializers.SerializerMethodField()
    company_summary = GSTCompanyImportSummarySerializer(source="company_import_summary", read_only=True)
    upload_status_message = serializers.SerializerMethodField()

    def get_tally_mapping(self, obj):
        return get_tally_mapping(obj.gst_return_type).as_dict()

    def get_upload_status_message(self, obj):
        if obj.total_rows and obj.duplicate_rows == obj.total_rows and not obj.imported_rows:
            return f"All {obj.total_rows} records are already imported for this company."
        if not obj.duplicate_rows:
            return ""
        return (
            f"Previously Processed File: {obj.duplicate_rows} vouchers were already "
            f"imported and will be skipped. {obj.imported_rows} corrected or retryable "
            "vouchers will continue for validation."
        )

    class Meta:
        model = GSTImportBatch
        fields = ["id", "file_name", "file_type", "gst_return_type", "total_rows", "imported_rows",
                  "failed_rows", "duplicate_rows", "company_gstin", "company_gstin_candidates", "company_details",
                  "company_resolution_status", "company_resolution_error", "tally_mapping",
                  "company_summary", "upload_status_message",
                  "file_hash", "file_size", "tax_period",
                  "uploaded_at", "created_at", "updated_at", "invoices"]

class GSTImportBatchListSerializer(GSTImportBatchSerializer):
    class Meta(GSTImportBatchSerializer.Meta):
        fields = [field for field in GSTImportBatchSerializer.Meta.fields if field != "invoices"]
