from django.db import migrations, models


def backfill_trip_locations(apps, schema_editor):
    Route = apps.get_model('transport', 'Route')
    for route in Route.objects.all():
        route.morning_start_location = route.morning_start_location or route.start_location
        route.morning_end_location = route.morning_end_location or route.end_location
        route.evening_start_location = route.evening_start_location or route.end_location
        route.evening_end_location = route.evening_end_location or route.start_location
        route.save(update_fields=[
            'morning_start_location',
            'morning_end_location',
            'evening_start_location',
            'evening_end_location',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('transport', '0007_transportarrivalalertlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='route',
            name='evening_end_location',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='route',
            name='evening_start_location',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='route',
            name='morning_end_location',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='route',
            name='morning_start_location',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='stop',
            name='trip_type',
            field=models.CharField(choices=[('Morning', 'Morning Pickup'), ('Evening', 'Evening Drop')], default='Morning', max_length=10),
        ),
        migrations.AlterModelOptions(
            name='stop',
            options={'ordering': ['trip_type', 'order_number']},
        ),
        migrations.RunPython(backfill_trip_locations, migrations.RunPython.noop),
    ]
