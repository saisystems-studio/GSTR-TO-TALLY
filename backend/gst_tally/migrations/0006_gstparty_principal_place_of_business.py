from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0005_gstparty_manual_verification")]
    operations = [
        migrations.AddField(model_name="gstparty", name="principal_place_of_business", field=models.TextField(blank=True)),
    ]
