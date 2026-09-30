from rest_framework import serializers

from .models import PromotionBatch, PromotionRecord


class PromotionClassMappingSerializer(serializers.Serializer):
    from_standard_id = serializers.IntegerField(min_value=1)
    to_standard_id = serializers.IntegerField(min_value=1)


class PromotionOverrideSerializer(serializers.Serializer):
    student_id = serializers.CharField(max_length=50)
    outcome = serializers.ChoiceField(choices=PromotionRecord.Outcome.choices)
    target_standard_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    target_section_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class PromotionPreviewRequestSerializer(serializers.Serializer):
    from_year_id = serializers.IntegerField(min_value=1)
    to_year_id = serializers.IntegerField(min_value=1)
    class_mappings = PromotionClassMappingSerializer(many=True, required=False, default=list)
    overrides = PromotionOverrideSerializer(many=True, required=False, default=list)
    section_mode = serializers.ChoiceField(
        choices=[("same_if_exists", "Same section if exists"), ("none", "No section")],
        default="same_if_exists",
    )
    unmapped_action = serializers.ChoiceField(
        choices=[("detain", "Detain"), ("left", "Mark as left"), ("error", "Mark as error")],
        default="detain",
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["from_year_id"] == attrs["to_year_id"]:
            raise serializers.ValidationError("From and To academic year cannot be the same.")
        return attrs


class PromotionRecordSerializer(serializers.ModelSerializer):
    student_id = serializers.CharField(source="student.student_id", read_only=True)
    student_name = serializers.CharField(source="student.student_name", read_only=True)
    from_standard_name = serializers.CharField(source="from_standard.name", read_only=True)
    from_section_name = serializers.SerializerMethodField()
    target_standard_name = serializers.SerializerMethodField()
    target_section_name = serializers.SerializerMethodField()

    class Meta:
        model = PromotionRecord
        fields = [
            "id",
            "student_id",
            "student_name",
            "from_standard_name",
            "from_section_name",
            "target_standard_name",
            "target_section_name",
            "outcome",
            "status",
            "error_message",
            "fee_total_amount",
            "fee_total_paid",
            "fee_total_due",
            "fee_pending_items",
            "fee_cleared_items",
            "fee_status",
        ]

    def get_from_section_name(self, obj):
        return obj.from_section.name if obj.from_section else None

    def get_target_standard_name(self, obj):
        return obj.target_standard.name if obj.target_standard else None

    def get_target_section_name(self, obj):
        return obj.target_section.name if obj.target_section else None


class PromotionBatchListSerializer(serializers.ModelSerializer):
    from_year_name = serializers.CharField(source="from_academic_year.name", read_only=True)
    to_year_name = serializers.CharField(source="to_academic_year.name", read_only=True)
    created_by_username = serializers.SerializerMethodField()

    class Meta:
        model = PromotionBatch
        fields = [
            "id",
            "from_year_name",
            "to_year_name",
            "status",
            "total_students",
            "ready_count",
            "error_count",
            "applied_count",
            "failed_count",
            "created_by_username",
            "created_at",
            "applied_at",
        ]

    def get_created_by_username(self, obj):
        return getattr(obj.created_by, "username", None)


class PromotionBatchDetailSerializer(PromotionBatchListSerializer):
    records = PromotionRecordSerializer(many=True, read_only=True)

    class Meta(PromotionBatchListSerializer.Meta):
        fields = PromotionBatchListSerializer.Meta.fields + ["notes", "records"]
