from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gst_tally", "0040_preview_batch_page_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="gstinvoice",
            index=models.Index(
                fields=["import_batch", "invoice_no"],
                name="gst_inv_batch_no_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="gstinvoice",
            index=models.Index(
                fields=["import_batch", "customer_gstin"],
                name="gst_inv_batch_gstin_idx",
            ),
        ),
    ]
