from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('timetable', '0004_classbreak'),
    ]

    operations = [
        migrations.AddField(
            model_name='timetableslot',
            name='combined_group',
            field=models.CharField(blank=True, db_index=True, max_length=36, null=True),
        ),
        migrations.RemoveConstraint(
            model_name='timetableslot',
            name='unique_teacher_slot_per_year',
        ),
        migrations.AddConstraint(
            model_name='timetableslot',
            constraint=models.UniqueConstraint(
                fields=('academic_year', 'teacher', 'day', 'period_no'),
                condition=Q(('combined_group__isnull', True), ('teacher__isnull', False)),
                name='unique_teacher_slot_per_year_no_group',
            ),
        ),
        migrations.AddConstraint(
            model_name='timetableslot',
            constraint=models.UniqueConstraint(
                fields=('academic_year', 'teacher', 'day', 'period_no', 'combined_group'),
                condition=Q(('combined_group__isnull', False), ('teacher__isnull', False)),
                name='unique_teacher_slot_per_year_group',
            ),
        ),
    ]
