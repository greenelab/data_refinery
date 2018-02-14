# Generated by Django 2.0.2 on 2018-02-14 17:47

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('data_refinery_common', '0025_remove_sample_has_derived'),
    ]

    operations = [
        migrations.CreateModel(
            name='OriginalFile',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_name', models.CharField(max_length=255)),
                ('absolute_file_path', models.CharField(blank=True, max_length=255, null=True)),
                ('size_in_bytes', models.IntegerField()),
                ('sha1', models.CharField(max_length=64)),
                ('source_archive_url', models.CharField(max_length=255)),
                ('source_filename', models.CharField(max_length=255)),
                ('source_absolute_file_path', models.CharField(max_length=255)),
                ('is_public', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('last_modified', models.DateTimeField(default=django.utils.timezone.now)),
                ('sample', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='data_refinery_common.Sample')),
            ],
            options={
                'db_table': 'original_files',
            },
        ),
    ]
