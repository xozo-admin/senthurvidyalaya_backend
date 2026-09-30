from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0011_school_scoped_identity_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='student',
            name='student_email',
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
    ]
