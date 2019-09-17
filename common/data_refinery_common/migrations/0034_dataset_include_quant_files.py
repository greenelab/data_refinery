# Generated by Django 2.1.8 on 2019-09-16 20:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0033_auto_20190913_1605'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='quant_sf_only',
            field=models.BooleanField(default=True, help_text='Include only quant.sf files in the generated dataset.'),
        ),
    ]
