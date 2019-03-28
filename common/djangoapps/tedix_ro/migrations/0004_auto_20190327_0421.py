# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2019-03-27 08:21
from __future__ import unicode_literals

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tedix_ro', '0003_studentprofile'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstructorProfile',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(max_length=15, validators=[django.core.validators.RegexValidator(message=b'Phone length should to be from 10 to 15', regex=b'^\\d{10,15}$')], verbose_name='phone')),
                ('city', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tedix_ro.City')),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tedix_ro.School')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='instructor_profile', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ParentProfile',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(max_length=15, validators=[django.core.validators.RegexValidator(message=b'Phone length should to be from 10 to 15', regex=b'^\\d{10,15}$')], verbose_name='phone')),
                ('city', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tedix_ro.City')),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tedix_ro.School')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RemoveField(
            model_name='studentparent',
            name='students',
        ),
        migrations.RemoveField(
            model_name='studentparent',
            name='user',
        ),
        migrations.RemoveField(
            model_name='studentprofile',
            name='teacher',
        ),
        migrations.DeleteModel(
            name='StudentParent',
        ),
        migrations.AddField(
            model_name='parentprofile',
            name='students',
            field=models.ManyToManyField(related_name='parents', to='tedix_ro.StudentProfile'),
        ),
        migrations.AddField(
            model_name='parentprofile',
            name='user',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='parent_profile', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='instructor',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='students', to='tedix_ro.InstructorProfile'),
        ),
    ]
