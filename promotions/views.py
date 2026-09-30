from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academics.models import Section, Standard
from school.models import AcademicYear
from schooladmin.permissions import IsAdmin
from students.models import Enrollment
from fees.models import StudentFee

from .models import PromotionBatch, PromotionRecord
from .serializers import (
    PromotionBatchDetailSerializer,
    PromotionBatchListSerializer,
    PromotionPreviewRequestSerializer,
)


MANUAL_PENDING_MARKER = "MANUAL_PENDING_BY_ADMIN"


def _recompute_batch_counts(batch: PromotionBatch):
    records = batch.records.all()
    batch.total_students = records.count()
    batch.ready_count = records.filter(status=PromotionRecord.RecordStatus.READY).count()
    batch.error_count = records.filter(status=PromotionRecord.RecordStatus.ERROR).count()
    batch.save(update_fields=["total_students", "ready_count", "error_count", "updated_at"])


class PromotionMetaView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        years = AcademicYear.objects.all().order_by("-start_date")
        standards = Standard.objects.prefetch_related(
            Prefetch("sections", queryset=Section.objects.order_by("name"))
        ).order_by("name")

        return Response(
            {
                "academic_years": [
                    {
                        "id": y.id,
                        "name": y.name,
                        "is_current": y.is_current,
                        "start_date": y.start_date,
                        "end_date": y.end_date,
                    }
                    for y in years
                ],
                "standards": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "sections": [
                            {"id": sec.id, "name": sec.name, "standard_id": s.id}
                            for sec in s.sections.all()
                        ],
                    }
                    for s in standards
                ],
            },
            status=status.HTTP_200_OK,
        )


class PromotionPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @staticmethod
    def _resolve_target_section(section_mode, source_section, target_standard):
        if not target_standard:
            return None, ""

        if section_mode == "none":
            return None, ""

        if not source_section:
            return None, ""

        section = Section.objects.filter(standard=target_standard, name=source_section.name).first()
        if section:
            return section, ""
        return None, f"Section '{source_section.name}' not found in target class '{target_standard.name}'."

    def post(self, request):
        serializer = PromotionPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from_year = get_object_or_404(AcademicYear, pk=data["from_year_id"])
        to_year = get_object_or_404(AcademicYear, pk=data["to_year_id"])

        mapping_by_from = {
            item["from_standard_id"]: item["to_standard_id"] for item in data.get("class_mappings", [])
        }
        overrides_by_student = {
            item["student_id"]: item for item in data.get("overrides", [])
        }

        standards_by_id = {s.id: s for s in Standard.objects.all()}

        # Use all enrollments in the selected source year.
        # Restricting to is_active=True makes historical/promoted years appear empty in preview.
        enrollments = Enrollment.objects.select_related("student", "standard", "section").filter(
            academic_year=from_year,
        )
        enrollment_list = list(enrollments)

        student_ids = [enr.student_id for enr in enrollment_list]
        fee_rows = StudentFee.objects.filter(
            student_id__in=student_ids,
            fee_definition__academic_year=from_year.name,
        ).values(
            "student_id",
            "total_amount",
            "paid_amount",
            "concession_amount",
        )

        fee_summary = defaultdict(
            lambda: {
                "total_amount": Decimal("0.00"),
                "total_paid": Decimal("0.00"),
                "total_due": Decimal("0.00"),
                "pending_items": 0,
                "cleared_items": 0,
                "status": "NO_FEES",
            }
        )

        for row in fee_rows:
            sid = row["student_id"]
            total_amount = row.get("total_amount") or Decimal("0.00")
            paid_amount = row.get("paid_amount") or Decimal("0.00")
            concession_amount = row.get("concession_amount") or Decimal("0.00")
            due_amount = total_amount - (paid_amount + concession_amount)
            if due_amount < Decimal("0.00"):
                due_amount = Decimal("0.00")

            summary = fee_summary[sid]
            summary["total_amount"] += total_amount
            summary["total_paid"] += paid_amount
            summary["total_due"] += due_amount
            if due_amount > Decimal("0.00"):
                summary["pending_items"] += 1
            else:
                summary["cleared_items"] += 1

        for sid, summary in fee_summary.items():
            if summary["pending_items"] > 0:
                summary["status"] = "PENDING"
            elif (summary["cleared_items"] > 0) and summary["total_due"] == Decimal("0.00"):
                summary["status"] = "CLEARED"
            else:
                summary["status"] = "NO_FEES"

        batch = PromotionBatch.objects.create(
            from_academic_year=from_year,
            to_academic_year=to_year,
            created_by=request.user,
            notes=data.get("notes", ""),
            status=PromotionBatch.BatchStatus.PREVIEW,
        )

        records_to_create = []
        ready_count = 0
        error_count = 0

        for enr in enrollment_list:
            override = overrides_by_student.get(enr.student.student_id)
            outcome = None
            target_standard = None
            target_section = None
            error_message = ""

            if override:
                outcome = override["outcome"]
                target_standard_id = override.get("target_standard_id")
                target_section_id = override.get("target_section_id")

                if target_standard_id:
                    target_standard = standards_by_id.get(target_standard_id)
                    if not target_standard:
                        error_message = f"Invalid target standard id: {target_standard_id}"

                if target_section_id and not error_message:
                    target_section = Section.objects.filter(pk=target_section_id).first()
                    if not target_section:
                        error_message = f"Invalid target section id: {target_section_id}"
                    elif target_standard and target_section.standard_id != target_standard.id:
                        error_message = "Target section does not belong to selected target class."

                if (
                    not error_message
                    and outcome in {PromotionRecord.Outcome.PROMOTED, PromotionRecord.Outcome.DETAINED}
                    and not target_standard
                ):
                    error_message = "Target class is required for promoted/detained override."
            else:
                mapped_target_id = mapping_by_from.get(enr.standard_id)
                if mapped_target_id:
                    target_standard = standards_by_id.get(mapped_target_id)
                    if target_standard is None:
                        error_message = f"Invalid mapped class id: {mapped_target_id}"
                    else:
                        outcome = (
                            PromotionRecord.Outcome.DETAINED
                            if target_standard.id == enr.standard_id
                            else PromotionRecord.Outcome.PROMOTED
                        )
                else:
                    if data["unmapped_action"] == "detain":
                        target_standard = enr.standard
                        outcome = PromotionRecord.Outcome.DETAINED
                    elif data["unmapped_action"] == "left":
                        outcome = PromotionRecord.Outcome.LEFT
                    else:
                        error_message = f"No class mapping found for class '{enr.standard.name}'."
                        outcome = PromotionRecord.Outcome.LEFT

                if not error_message and outcome in {
                    PromotionRecord.Outcome.PROMOTED,
                    PromotionRecord.Outcome.DETAINED,
                }:
                    target_section, section_error = self._resolve_target_section(
                        data["section_mode"], enr.section, target_standard
                    )
                    if section_error:
                        error_message = section_error

            if outcome in {PromotionRecord.Outcome.GRADUATED, PromotionRecord.Outcome.LEFT}:
                target_standard = None
                target_section = None

            status_value = PromotionRecord.RecordStatus.ERROR if error_message else PromotionRecord.RecordStatus.READY
            if status_value == PromotionRecord.RecordStatus.ERROR:
                error_count += 1
            else:
                ready_count += 1

            records_to_create.append(
                PromotionRecord(
                    batch=batch,
                    student=enr.student,
                    from_enrollment=enr,
                    from_standard=enr.standard,
                    from_section=enr.section,
                    target_standard=target_standard,
                    target_section=target_section,
                    outcome=outcome or PromotionRecord.Outcome.LEFT,
                    status=status_value,
                    error_message=error_message,
                    fee_total_amount=fee_summary[enr.student_id]["total_amount"],
                    fee_total_paid=fee_summary[enr.student_id]["total_paid"],
                    fee_total_due=fee_summary[enr.student_id]["total_due"],
                    fee_pending_items=fee_summary[enr.student_id]["pending_items"],
                    fee_cleared_items=fee_summary[enr.student_id]["cleared_items"],
                    fee_status=fee_summary[enr.student_id]["status"],
                )
            )

        PromotionRecord.objects.bulk_create(records_to_create)

        batch.total_students = len(records_to_create)
        batch.ready_count = ready_count
        batch.error_count = error_count
        batch.save(update_fields=["total_students", "ready_count", "error_count", "updated_at"])

        detail = PromotionBatch.objects.select_related(
            "from_academic_year", "to_academic_year", "created_by"
        ).prefetch_related(
            "records__student",
            "records__from_standard",
            "records__from_section",
            "records__target_standard",
            "records__target_section",
        ).get(pk=batch.pk)
        return Response(PromotionBatchDetailSerializer(detail).data, status=status.HTTP_201_CREATED)


class PromotionBatchListView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        queryset = PromotionBatch.objects.select_related(
            "from_academic_year", "to_academic_year", "created_by"
        ).all()[:50]
        return Response(PromotionBatchListSerializer(queryset, many=True).data, status=status.HTTP_200_OK)


class PromotionBatchDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, batch_id):
        batch = get_object_or_404(
            PromotionBatch.objects.select_related(
                "from_academic_year",
                "to_academic_year",
                "created_by",
            ).prefetch_related(
                "records__student",
                "records__from_standard",
                "records__from_section",
                "records__target_standard",
                "records__target_section",
            ),
            pk=batch_id,
        )
        return Response(PromotionBatchDetailSerializer(batch).data, status=status.HTTP_200_OK)


class PromotionRecordStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @transaction.atomic
    def patch(self, request, batch_id, record_id):
        batch = get_object_or_404(PromotionBatch, pk=batch_id)
        record = get_object_or_404(PromotionRecord, pk=record_id, batch_id=batch_id)
        if record.status == PromotionRecord.RecordStatus.APPLIED:
            return Response(
                {"error": "Cannot edit status for a student that is already applied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row_status = (request.data.get("row_status") or "").upper().strip()

        if row_status not in {"READY", "PENDING", "LEFT", "ERROR"}:
            return Response(
                {"error": "row_status must be one of READY, PENDING, LEFT, ERROR."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if row_status == "LEFT":
            record.outcome = PromotionRecord.Outcome.LEFT
            record.target_standard = None
            record.target_section = None
            record.status = PromotionRecord.RecordStatus.READY
            record.error_message = ""
        elif row_status == "PENDING":
            record.status = PromotionRecord.RecordStatus.ERROR
            record.error_message = MANUAL_PENDING_MARKER
        elif row_status == "ERROR":
            record.status = PromotionRecord.RecordStatus.ERROR
            if not record.error_message or record.error_message == MANUAL_PENDING_MARKER:
                record.error_message = "Marked as error by admin."
        else:  # READY
            record.status = PromotionRecord.RecordStatus.READY
            if record.error_message == MANUAL_PENDING_MARKER or record.error_message == "Marked as error by admin.":
                record.error_message = ""

        record.save(
            update_fields=[
                "outcome",
                "target_standard",
                "target_section",
                "status",
                "error_message",
                "updated_at",
            ]
        )
        _recompute_batch_counts(batch)

        detail = PromotionBatch.objects.select_related(
            "from_academic_year", "to_academic_year", "created_by"
        ).prefetch_related(
            "records__student",
            "records__from_standard",
            "records__from_section",
            "records__target_standard",
            "records__target_section",
        ).get(pk=batch.pk)
        return Response(PromotionBatchDetailSerializer(detail).data, status=status.HTTP_200_OK)


class PromotionBatchDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @transaction.atomic
    def delete(self, request, batch_id):
        batch = get_object_or_404(PromotionBatch, pk=batch_id)

        if batch.status == PromotionBatch.BatchStatus.APPLIED:
            return Response(
                {"error": "Applied promotion batches cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deleted_id = batch.id
        deleted_records, _ = batch.records.all().delete()
        batch.delete()
        return Response(
            {
                "message": "Promotion batch and related records deleted successfully.",
                "batch_id": deleted_id,
                "deleted_related_records": deleted_records,
            },
            status=status.HTTP_200_OK,
        )


class PromotionBatchApplyView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @transaction.atomic
    def post(self, request, batch_id):
        batch = get_object_or_404(
            PromotionBatch.objects.select_related("from_academic_year", "to_academic_year"),
            pk=batch_id,
        )

        records = list(
            batch.records.select_related(
                "student",
                "from_enrollment",
                "target_standard",
                "target_section",
            ).all()
        )

        applied_count = 0
        failed_count = 0

        for record in records:
            if record.status == PromotionRecord.RecordStatus.APPLIED:
                # Already processed in a previous apply run.
                continue

            if record.status == PromotionRecord.RecordStatus.ERROR:
                failed_count += 1
                continue

            try:
                if record.outcome in {PromotionRecord.Outcome.PROMOTED, PromotionRecord.Outcome.DETAINED}:
                    if not record.target_standard:
                        raise ValueError("Target class is required for promoted/detained outcome.")
                    if record.target_section and record.target_section.standard_id != record.target_standard_id:
                        raise ValueError("Target section does not belong to target class.")

                    to_enrollment, _ = Enrollment.objects.update_or_create(
                        student=record.student,
                        academic_year=batch.to_academic_year,
                        defaults={
                            "standard": record.target_standard,
                            "section": record.target_section,
                            "is_active": True,
                            "promoted": False,
                        },
                    )
                    record.to_enrollment = to_enrollment

                    # Keep profile aligned with latest active enrollment for compatibility with existing modules.
                    record.student.standard = record.target_standard
                    record.student.section = record.target_section
                    record.student.save(update_fields=["standard", "section"])

                elif record.outcome in {PromotionRecord.Outcome.GRADUATED, PromotionRecord.Outcome.LEFT}:
                    # Student has no active enrollment in the target year.
                    Enrollment.objects.filter(
                        student=record.student,
                        academic_year=batch.to_academic_year,
                    ).update(is_active=False)
                    record.student.standard = None
                    record.student.section = None
                    record.student.save(update_fields=["standard", "section"])

                record.from_enrollment.is_active = False
                record.from_enrollment.promoted = record.outcome == PromotionRecord.Outcome.PROMOTED
                record.from_enrollment.save(update_fields=["is_active", "promoted", "updated_at"])

                record.status = PromotionRecord.RecordStatus.APPLIED
                record.error_message = ""
                record.applied_at = timezone.now()
                record.save(update_fields=["to_enrollment", "status", "error_message", "applied_at", "updated_at"])
                applied_count += 1
            except Exception as exc:
                record.status = PromotionRecord.RecordStatus.ERROR
                record.error_message = str(exc)
                record.save(update_fields=["status", "error_message", "updated_at"])
                failed_count += 1

        total_records = batch.records.count()
        total_applied = batch.records.filter(status=PromotionRecord.RecordStatus.APPLIED).count()
        total_errors = batch.records.filter(status=PromotionRecord.RecordStatus.ERROR).count()

        batch.applied_count = total_applied
        batch.failed_count = total_errors
        batch.applied_at = timezone.now() if total_applied > 0 else None
        if total_records > 0 and total_applied == total_records:
            batch.status = PromotionBatch.BatchStatus.APPLIED
        elif total_applied > 0:
            batch.status = PromotionBatch.BatchStatus.PARTIAL
        elif total_errors > 0:
            batch.status = PromotionBatch.BatchStatus.FAILED
        else:
            batch.status = PromotionBatch.BatchStatus.PREVIEW

        batch.save(update_fields=["applied_count", "failed_count", "applied_at", "status", "updated_at"])

        detail = PromotionBatch.objects.select_related(
            "from_academic_year", "to_academic_year", "created_by"
        ).prefetch_related(
            "records__student",
            "records__from_standard",
            "records__from_section",
            "records__target_standard",
            "records__target_section",
        ).get(pk=batch.pk)
        return Response(PromotionBatchDetailSerializer(detail).data, status=status.HTTP_200_OK)
