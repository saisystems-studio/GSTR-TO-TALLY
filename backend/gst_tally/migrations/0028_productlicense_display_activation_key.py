from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gst_tally", "0027_product_license_security"),
    ]

    operations = [
        migrations.AddField(
            model_name="productlicense",
            name="display_activation_key",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
