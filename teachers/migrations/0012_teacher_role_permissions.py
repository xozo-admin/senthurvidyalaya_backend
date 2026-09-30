# Generated manually for teacher role permission controls.

from django.db import migrations, models
import teachers.models


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0011_teacher_school_teacherallocation_school'),
    ]

    operations = [
        migrations.AddField(
            model_name='teacher',
            name='role_permissions',
            field=models.JSONField(blank=True, default=teachers.models.default_teacher_role_permissions),
        ),
    ]
