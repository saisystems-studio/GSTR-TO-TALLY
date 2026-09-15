# Generated for the outbound Windows Tally Agent transport.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0037_localtallyagent_localtallyjob_and_more")]
    operations = [
        migrations.AddField(
            model_name="tallyimportjob",
            name="local_agent",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="import_jobs", to="gst_tally.localtallyagent"),
        ),
    ]
