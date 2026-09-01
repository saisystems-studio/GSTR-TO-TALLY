from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0008_alter_gstparty_lookup_status")]

    operations = [
        migrations.AddField(
            model_name="gstimportbatch",
            name="source_parties",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
