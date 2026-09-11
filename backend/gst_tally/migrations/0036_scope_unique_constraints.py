from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gst_tally", "0035_alter_gstcompanyimportsummary_company_scope_id_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="gstcompanyimportsummary",
            name="uniq_gst_company_summary",
        ),
        migrations.AddConstraint(
            model_name="gstcompanyimportsummary",
            constraint=models.UniqueConstraint(
                fields=["company_scope_id", "company_gstin", "return_type", "import_scope_hash"],
                name="uniq_gst_company_summary",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="gsttallyvoucherregistry",
            name="uniq_gst_voucher_registry",
        ),
        migrations.AddConstraint(
            model_name="gsttallyvoucherregistry",
            constraint=models.UniqueConstraint(
                fields=["company_scope_id", "company_gstin", "return_type", "voucher_identity_hash"],
                name="uniq_gst_voucher_registry",
            ),
        ),
    ]
