from django.contrib import admin

from .models import PromotionBatch, PromotionRecord


@admin.register(PromotionBatch)
class PromotionBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "from_academic_year",
        "to_academic_year",
        "status",
        "total_students",
        "ready_count",
        "error_count",
        "applied_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "from_academic_year", "to_academic_year")
    search_fields = ("from_academic_year__name", "to_academic_year__name")


@admin.register(PromotionRecord)
class PromotionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "batch",
        "student",
        "outcome",
        "status",
        "from_standard",
        "target_standard",
    )
    list_filter = ("status", "outcome", "batch")
    search_fields = ("student__student_id", "student__student_name")

