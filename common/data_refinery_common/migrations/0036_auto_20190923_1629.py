# Generated by Django 2.1.8 on 2019-09-23 16:29

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0035_auto_20190919_1848'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='computedfile',
            index=models.Index(fields=['filename'], name='computed_fi_filenam_64958d_idx'),
        ),
    ]