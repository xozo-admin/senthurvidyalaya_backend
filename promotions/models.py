from decimal import Decimal

from django.conf import settings
from django.db import models


class PromotionBatch(models.Model):
    class BatchStatus(models.TextChoices):
        PREVIEW = "PREVIEW", "Preview"
        APPLIED = "APPLIED", "Applied"
        PARTIAL = "PARTIAL", "Partially Applied"
        FAILED = "FAILED", "Failed"

    from_academic_year = models.ForeignKey(
        "school.AcademicYear",
        on_delete=models.PROTECT,
        related_name="promotion_batches_from",
    )
    to_academic_year = models.ForeignKey(
        "school.AcademicYear",
        on_delete=models.PROTECT,
        related_name="promotion_batches_to",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_promotion_batches",
    )
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=BatchStatus.choices, default=BatchStatus.PREVIEW)
    total_students = models.PositiveIntegerField(default=0)
    ready_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    applied_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PromotionBatch #{self.pk} {self.from_academic_year.name} -> {self.to_academic_year.name}"


class PromotionRecord(models.Model):
    class Outcome(models.TextChoices):
        PROMOTED = "PROMOTED", "Promoted"
        DETAINED = "DETAINED", "Detained"
        GRADUATED = "GRADUATED", "Graduated"
        LEFT = "LEFT", "Left School"

    class RecordStatus(models.TextChoices):
        READY = "READY", "Ready"
        ERROR = "ERROR", "Error"
        APPLIED = "APPLIED", "Applied"

    batch = models.ForeignKey(PromotionBatch, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="promotion_records")
    from_enrollment = models.ForeignKey(
        "students.Enrollment",
        on_delete=models.PROTECT,
        related_name="promotion_from_records",
    )
    to_enrollment = models.ForeignKey(
        "students.Enrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotion_to_records",
    )

    from_standard = models.ForeignKey(
        "academics.Standard",
        on_delete=models.PROTECT,
        related_name="promotion_records_from_standard",
    )
    from_section = models.ForeignKey(
        "academics.Section",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_records_from_section",
    )
    target_standard = models.ForeignKey(
        "academics.Standard",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_records_target_standard",
    )
    target_section = models.ForeignKey(
        "academics.Section",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_records_target_section",
    )

    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.READY)
    error_message = models.TextField(blank=True, default="")
    fee_total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    fee_total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    fee_total_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    fee_pending_items = models.PositiveIntegerField(default=0)
    fee_cleared_items = models.PositiveIntegerField(default=0)
    fee_status = models.CharField(max_length=20, default="NO_FEES")
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("batch", "student")]
        ordering = ["student__student_id"]

    def __str__(self):
        return f"{self.student.student_id} {self.from_standard.name} -> {getattr(self.target_standard, 'name', '-')}"
