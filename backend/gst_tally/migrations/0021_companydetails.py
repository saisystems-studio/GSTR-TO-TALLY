from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gst_tally', '0020_tallycompanymapping_license_administrator_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyDetails',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_name', models.CharField(max_length=255, unique=True)),
                ('gstin', models.CharField(blank=True, max_length=15)),
                ('state', models.CharField(blank=True, max_length=100)),
                ('financial_year', models.CharField(blank=True, max_length=40)),
                ('financial_year_from', models.CharField(blank=True, max_length=20)),
                ('financial_year_to', models.CharField(blank=True, max_length=20)),
                ('tally_serial_number', models.CharField(blank=True, max_length=64)),
                ('tally_edition', models.CharField(blank=True, max_length=50)),
                ('tss_status', models.CharField(blank=True, max_length=50)),
                ('license_administrator', models.CharField(blank=True, max_length=255)),
                ('tally_connected', models.BooleanField(default=False)),
                ('company_verified', models.BooleanField(default=False)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'companydetails_tbl',
            },
        ),
    ]
