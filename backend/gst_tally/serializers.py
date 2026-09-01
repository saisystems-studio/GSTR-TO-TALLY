from rest_framework import serializers
from .models import GSTImportBatch, GSTInvoice
from .tally.return_mapping import get_tally_mapping

class GSTInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTInvoice
        exclude = ["source_type", "import_batch"]

class GSTImportBatchSerializer(serializers.ModelSerializer):
    invoices = GSTInvoiceSerializer(many=True, read_only=True)
    tally_mapping = serializers.SerializerMethodField()

    def get_tally_mapping(self, obj):
        return get_tally_mapping(obj.gst_return_type).as_dict()
    class Meta:
        model = GSTImportBatch
        fields = ["id", "file_name", "file_type", "gst_return_type", "total_rows", "imported_rows",
                  "failed_rows", "company_gstin", "company_gstin_candidates", "company_details",
                  "company_resolution_status", "company_resolution_error", "tally_mapping",
                  "uploaded_at", "created_at", "updated_at", "invoices"]

class GSTImportBatchListSerializer(GSTImportBatchSerializer):
    class Meta(GSTImportBatchSerializer.Meta):
        fields = [field for field in GSTImportBatchSerializer.Meta.fields if field != "invoices"]
