# Generated by Django 2.0.2 on 2018-02-19 15:06

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0039_auto_20180216_2207'),
    ]

    operations = [
        migrations.AddField(
            model_name='computationalresult',
            name='program_version',
            field=models.CharField(default='1', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='sampleresultassociation',
            name='result',
            field=models.ForeignKey(default='', on_delete=django.db.models.deletion.CASCADE, to='data_refinery_common.ComputationalResult'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='sampleresultassociation',
            name='sample',
            field=models.ForeignKey(default='', on_delete=django.db.models.deletion.CASCADE, to='data_refinery_common.Sample'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='organism',
            name='name',
            field=models.CharField(max_length=256, unique=True),
        ),
        migrations.AlterField(
            model_name='organism',
            name='taxonomy_id',
            field=models.IntegerField(unique=True),
        ),
    ]
