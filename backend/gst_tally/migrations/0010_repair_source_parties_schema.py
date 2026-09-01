from django.db import migrations


def repair_source_parties(apps, schema_editor):
    if schema_editor.connection.vendor != "microsoft":
        return
    schema_editor.execute("""
        IF COL_LENGTH('dbo.gst_import_batch_tbl', 'source_parties') IS NULL
        BEGIN
            ALTER TABLE [dbo].[gst_import_batch_tbl]
            ADD [source_parties] nvarchar(max)
                CONSTRAINT [DF_gst_import_batch_source_parties] DEFAULT '{}' NOT NULL;
            ALTER TABLE [dbo].[gst_import_batch_tbl]
            ADD CONSTRAINT [CK_gst_import_batch_source_parties_json]
                CHECK (ISJSON([source_parties]) = 1);
        END
    """)


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0009_gstimportbatch_source_parties")]

    operations = [
        migrations.RunPython(repair_source_parties, migrations.RunPython.noop),
    ]
