# -*- coding: utf-8 -*-
# Generated by Django 1.11.1 on 2018-02-09 15:27
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0009_auto_20180208_1840'),
    ]

    operations = [
        migrations.AddField(
            model_name='sample',
            name='has_prederived',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sample',
            name='has_raw',
            field=models.BooleanField(default=True),
        ),
    ]
