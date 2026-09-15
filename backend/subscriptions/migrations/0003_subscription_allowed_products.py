from django.db import migrations, models


def preserve_registered_products(apps, schema_editor):
    Subscription = apps.get_model('subscriptions', 'Subscription')
    ProductLicense = apps.get_model('gst_tally', 'ProductLicense')
    alias = schema_editor.connection.alias
    for subscription in Subscription.objects.using(alias).all().iterator():
        used = (ProductLicense.objects.using(alias).filter(customer_id=subscription.user_id)
                .exclude(status='REVOKED').exclude(licensed_tally_serial='')
                .values('licensed_tally_serial').distinct().count())
        if used > subscription.allowed_products:
            subscription.allowed_products = used
            subscription.save(using=alias, update_fields=['allowed_products'])


class Migration(migrations.Migration):
    dependencies = [('subscriptions', '0002_renewalhistory_notes'),
                    ('gst_tally', '0029_gstimportbatch_file_hash_license')]
    operations = [
        migrations.AddField(model_name='subscription', name='allowed_products',
                            field=models.PositiveSmallIntegerField(default=1)),
        migrations.RunPython(preserve_registered_products, migrations.RunPython.noop),
    ]
