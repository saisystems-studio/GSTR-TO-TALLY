from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gst_tally", "0033_gstcompanyimportsummary_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="gstimportbatch",
            name="company_scope_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="gstcompanyimportsummary",
            name="company_scope_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="gsttallyvoucherregistry",
            name="company_scope_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
