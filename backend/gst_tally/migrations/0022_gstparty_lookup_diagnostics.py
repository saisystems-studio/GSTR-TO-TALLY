import django.core.serializers.json
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gst_tally', '0021_companydetails'),
    ]

    operations = [
        migrations.AddField(
            model_name='gstparty',
            name='lookup_diagnostics',
            field=models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
        ),
    ]
