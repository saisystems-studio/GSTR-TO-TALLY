from django.db import migrations, models
import django.db.models.deletion


def attach_or_remove_legacy_registry_rows(apps, schema_editor):
    Registry = apps.get_model("gst_tally", "GSTTallyVoucherRegistry")
    for row in Registry.objects.filter(batch__isnull=True).iterator(chunk_size=1000):
        if row.latest_invoice_id:
            row.batch_id = row.latest_invoice.import_batch_id
            row.save(update_fields=["batch"])
        else:
            row.delete()


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0038_tallyimportjob_local_agent")]

    operations = [
        migrations.AlterField(
            model_name="gstcompanyimportsummary",
            name="import_scope_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RemoveConstraint(
            model_name="gstcompanyimportsummary",
            name="uniq_gst_company_summary",
        ),
        migrations.AddField(
            model_name="gstinvoice",
            name="processing_state",
            field=models.CharField(db_index=True, default="PENDING", max_length=20),
        ),
        migrations.AlterField(
            model_name="tallyvouchermapping",
            name="invoice",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tally_vouchers", to="gst_tally.gstinvoice"),
        ),
        migrations.AddField(
            model_name="gsttallyvoucherregistry",
            name="batch",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="voucher_registry_entries", to="gst_tally.gstimportbatch"),
        ),
        migrations.RunPython(attach_or_remove_legacy_registry_rows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="gsttallyvoucherregistry",
            name="batch",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="voucher_registry_entries", to="gst_tally.gstimportbatch"),
        ),
        migrations.RemoveConstraint(
            model_name="gsttallyvoucherregistry",
            name="uniq_gst_voucher_registry",
        ),
        migrations.AddConstraint(
            model_name="gsttallyvoucherregistry",
            constraint=models.UniqueConstraint(fields=("batch", "company_scope_id", "company_gstin", "return_type", "voucher_identity_hash"), name="uniq_gst_voucher_registry"),
        ),
    ]
