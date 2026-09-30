from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('timetable', '0005_combined_group'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='timetableslot',
            name='unique_teacher_slot_per_year_group',
        ),
    ]
