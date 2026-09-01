from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0015_gstparty_jurisdictions")]
    operations = [
        migrations.AddField(model_name="gstinvoice", name="item_name", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="gstinvoice", name="description", field=models.TextField(blank=True)),
        migrations.AddField(model_name="gstinvoice", name="hsn_sac", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="gstinvoice", name="quantity", field=models.DecimalField(blank=True, decimal_places=3, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="unit", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="gstinvoice", name="rate", field=models.DecimalField(blank=True, decimal_places=4, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="discount", field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="cess", field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="place_of_supply", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="gstinvoice", name="supply_type", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="gstinvoice", name="other_charges", field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="round_off", field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
        migrations.AddField(model_name="gstinvoice", name="source_line", field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(name="TallyVoucherMapping", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("idempotency_key", models.CharField(max_length=64, unique=True)), ("source_invoice_number", models.CharField(max_length=100)),
            ("party_gstin", models.CharField(blank=True, max_length=15)), ("tally_company", models.CharField(max_length=255)),
            ("tally_voucher_identifier", models.CharField(blank=True, max_length=255)), ("import_status", models.CharField(max_length=30)),
            ("raw_response", models.TextField(blank=True)), ("error_message", models.TextField(blank=True)),
            ("imported_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tally_vouchers", to="gst_tally.gstimportbatch")),
            ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tally_vouchers", to="gst_tally.gstinvoice")),
        ], options={"db_table": "tally_voucher_mapping_tbl"}),
        migrations.AddIndex(model_name="tallyvouchermapping", index=models.Index(fields=["tally_company", "source_invoice_number"], name="tally_company_inv_idx")),
    ]
