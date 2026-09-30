from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salary', '0006_alter_staffsalarypayment_payment_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffsalarypayment',
            name='transfer_bank_code',
            field=models.CharField(default='DEFAULT', max_length=20),
        ),
        migrations.AddField(
            model_name='teachersalarypayment',
            name='transfer_bank_code',
            field=models.CharField(default='DEFAULT', max_length=20),
        ),
    ]
