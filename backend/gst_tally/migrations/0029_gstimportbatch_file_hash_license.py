from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gst_tally", "0028_productlicense_display_activation_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="gstimportbatch",
            name="file_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="gstimportbatch",
            name="file_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="gstimportbatch",
            name="product_license",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="import_batches", to="gst_tally.productlicense"),
        ),
        migrations.AddIndex(
            model_name="gstimportbatch",
            index=models.Index(fields=["product_license", "company_gstin", "file_hash"], name="gst_batch_file_hash_idx"),
        ),
    ]
