from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salary', '0007_staffsalarypayment_transfer_bank_code_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='salarystructure',
            name='pending_base_salary',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='salarystructure',
            name='pending_delete',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salarystructure',
            name='pending_effective_month',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salarystructure',
            name='pending_effective_year',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salarystructure',
            name='pending_late_penalty_percentage',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teachersalarystructure',
            name='pending_base_salary',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='teachersalarystructure',
            name='pending_delete',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='teachersalarystructure',
            name='pending_effective_month',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teachersalarystructure',
            name='pending_effective_year',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teachersalarystructure',
            name='pending_late_penalty_percentage',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
