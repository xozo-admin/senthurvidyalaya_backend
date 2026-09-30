from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fees', '0007_alter_studentfee_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='feedefinition',
            name='installment_count',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='feepayment',
            name='installment_number',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
