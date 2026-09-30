from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("academics", "0002_remove_section_class_teacher_classteacher"),
        ("school", "0002_academicyear"),
        ("students", "0007_student_accommodation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PromotionBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PREVIEW", "Preview"),
                            ("APPLIED", "Applied"),
                            ("PARTIAL", "Partially Applied"),
                            ("FAILED", "Failed"),
                        ],
                        default="PREVIEW",
                        max_length=20,
                    ),
                ),
                ("total_students", models.PositiveIntegerField(default=0)),
                ("ready_count", models.PositiveIntegerField(default=0)),
                ("error_count", models.PositiveIntegerField(default=0)),
                ("applied_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_promotion_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_academic_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_batches_from",
                        to="school.academicyear",
                    ),
                ),
                (
                    "to_academic_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_batches_to",
                        to="school.academicyear",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PromotionRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("PROMOTED", "Promoted"),
                            ("DETAINED", "Detained"),
                            ("GRADUATED", "Graduated"),
                            ("LEFT", "Left School"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("READY", "Ready"), ("ERROR", "Error"), ("APPLIED", "Applied")],
                        default="READY",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="records",
                        to="promotions.promotionbatch",
                    ),
                ),
                (
                    "from_enrollment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_from_records",
                        to="students.enrollment",
                    ),
                ),
                (
                    "from_section",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_records_from_section",
                        to="academics.section",
                    ),
                ),
                (
                    "from_standard",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_records_from_standard",
                        to="academics.standard",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_records",
                        to="students.student",
                    ),
                ),
                (
                    "target_section",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_records_target_section",
                        to="academics.section",
                    ),
                ),
                (
                    "target_standard",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="promotion_records_target_standard",
                        to="academics.standard",
                    ),
                ),
                (
                    "to_enrollment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="promotion_to_records",
                        to="students.enrollment",
                    ),
                ),
            ],
            options={"ordering": ["student__student_id"]},
        ),
        migrations.AlterUniqueTogether(
            name="promotionrecord",
            unique_together={("batch", "student")},
        ),
    ]
