import decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("promotions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_cleared_items",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_pending_items",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_status",
            field=models.CharField(default="NO_FEES", max_length=20),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_total_amount",
            field=models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_total_due",
            field=models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="fee_total_paid",
            field=models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12),
        ),
    ]

