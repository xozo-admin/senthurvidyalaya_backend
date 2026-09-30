from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_passwordresetotp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='user_type',
            field=models.CharField(
                choices=[
                    ('super_admin', 'Super Admin'),
                    ('admin', 'Admin'),
                    ('teacher', 'Teacher'),
                    ('student', 'Student'),
                    ('staff', 'Non Teaching Staff'),
                ],
                default='student',
                max_length=20,
            ),
        ),
    ]
