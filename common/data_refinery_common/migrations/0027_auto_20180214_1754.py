# Generated by Django 2.0.2 on 2018-02-14 17:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0026_originalfile'),
    ]

    operations = [
        migrations.AlterField(
            model_name='originalfile',
            name='size_in_bytes',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
