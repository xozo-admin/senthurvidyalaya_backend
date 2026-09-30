# fees/views.py - Updated with Razorpay integration

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Q, Count, F, DecimalField, ExpressionWrapper, Max, Value
from django.db.models.functions import Greatest
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.shortcuts import get_object_or_404
from decimal import Decimal, InvalidOperation
import re
import logging
from datetime import datetime, timedelta
import uuid

# Razorpay client
from .razorpay_client import RazorpayClient

# Models
from .models import (
    FeeDefinition,
    StudentFee,
    FeePayment,
    FeeConcession,
    FeeReminder,
    FeeRefundEvent,
)
from .services import send_due_fee_reminders
from students.models import Student
from academics.models import Standard, Section
from hostel.models import HostelAllocation

# Serializers
from .serializers import (
    FeeDefinitionSerializer, StudentFeeReportSerializer, 
    PaymentReceiptSerializer, PaymentInitiationSerializer,
    PaymentVerificationSerializer, FeeConcessionSerializer,
    BulkFeeAssignmentSerializer, DueDateExtensionSerializer
)

# Permissions
from staff.permissions import IsOwnerAdminOrFinanceAdminStaff, IsOwnerAdminOrFinanceAdminStaffOrHostelWarden
from audit.models import AuditLog

logger = logging.getLogger(__name__)

# Initialize Razorpay client
razorpay = RazorpayClient()
HOSTEL_FEE_TYPE = 'Hostel'


def _normalize_fee_type(fee_type):
    value = str(fee_type or '').strip()
    return HOSTEL_FEE_TYPE if value.lower() == 'hostel' else value


def _is_hostel_fee_type(fee_type):
    return _normalize_fee_type(fee_type) == HOSTEL_FEE_TYPE


def _student_standard(student):
    if getattr(student, 'section', None) and getattr(student.section, 'standard', None):
        return student.section.standard
    return getattr(student, 'standard', None)


def _hostel_allocations_for_year(academic_year, class_name=None):
    allocations = HostelAllocation.objects.filter(
        is_active=True,
        academic_year__name=academic_year,
    ).select_related(
        'student',
        'student__standard',
        'student__section',
        'student__section__standard',
        'academic_year',
    )

    if class_name:
        allocations = allocations.filter(
            Q(student__section__standard__name=class_name) |
            Q(student__standard__name=class_name)
        )

    return allocations


def _resolve_hostel_ledger(student, academic_year, class_name=''):
    ledgers = StudentFee.objects.filter(
        student=student,
        fee_definition__academic_year=academic_year,
        fee_definition__fee_type__iexact=HOSTEL_FEE_TYPE,
        fee_definition__is_active=True,
    ).select_related('fee_definition', 'fee_definition__standard')

    if class_name:
        ledgers = ledgers.filter(fee_definition__standard__name=class_name)

    count = ledgers.count()
    if count == 1:
        return ledgers.first(), None
    if count > 1:
        standard = _student_standard(student)
        if standard:
            narrowed = ledgers.filter(fee_definition__standard=standard)
            if narrowed.count() == 1:
                return narrowed.first(), None
        return None, {"error": "Multiple hostel fee records found for this student. Please provide the class name."}

    has_allocation = _hostel_allocations_for_year(academic_year).filter(student=student).exists()
    if not has_allocation:
        return None, {"error": "Student does not have an active hostel allocation for the selected academic year."}
    return None, {"error": "Hostel fee record not found for this student in the selected academic year."}


def _installment_payment_context(ledger):
    successful_payments = ledger.payments.filter(payment_status='SUCCESS').count()
    return {
        "installment_count": ledger.installment_count,
        "installments_paid_count": successful_payments,
        "installments_remaining_count": max(ledger.installment_count - successful_payments, 0),
        "next_installment_number": ledger.next_installment_number,
        "next_installment_amount": ledger.next_installment_amount,
    }


def _validate_installment_payment_amount(ledger, amount):
    installment_context = _installment_payment_context(ledger)
    full_due_amount = ledger.due_amount.quantize(Decimal('0.01'))
    next_installment_amount = installment_context["next_installment_amount"].quantize(Decimal('0.01'))
    if ledger.due_amount <= Decimal('0.00'):
        return {
            "error": "This fee is already fully paid.",
            **installment_context,
        }

    if amount not in {next_installment_amount, full_due_amount}:
        return {
            "error": (
                "Amount must be either the next installment "
                f"(₹{next_installment_amount}) or full due (₹{full_due_amount})."
            ),
            "next_installment_amount": next_installment_amount,
            "full_due_amount": full_due_amount,
            **installment_context,
        }

    return None


def _payment_installment_number(ledger, amount):
    next_number = ledger.next_installment_number
    due_amount = ledger.due_amount.quantize(Decimal('0.01'))
    if amount == due_amount and due_amount > Decimal('0.00'):
        return ledger.installment_count
    return next_number


def _student_primary_phone(student):
    return getattr(student, 'father_phone', '') or getattr(student, 'mother_phone', '') or ''


def _student_roll_number(student):
    extra_details = getattr(student, 'extra_details', {})
    if isinstance(extra_details, dict):
        return extra_details.get('roll_number', '')
    return ''


def _resolve_active_fee_definition(standard, fee_type, academic_year):
    """
    Resolve an active fee definition with year-scoped lookup.
    Returns (fee_definition, error_response_payload_or_none).
    """
    fee_defs = FeeDefinition.objects.filter(
        standard=standard,
        fee_type__iexact=_normalize_fee_type(fee_type),
        academic_year=academic_year,
        is_active=True,
    )

    if not fee_defs.exists():
        return None, {"error": "Fee definition not found"}

    fee_def = fee_defs.first()
    return fee_def, None


def _log_fee_audit(action, request=None, details=None, severity='INFO', response_status=None):
    """
    Centralized audit logging for fees operations.
    This must never break fee flows if audit logging fails.
    """
    user = None
    if request is not None:
        req_user = getattr(request, 'user', None)
        if getattr(req_user, 'is_authenticated', False):
            user = req_user

    payload = {"module": "fees"}
    if isinstance(details, dict):
        payload.update(details)
    elif details is not None:
        payload["message"] = str(details)

    try:
        AuditLog.log(
            user=user,
            action=action,
            details=payload,
            request=request,
            severity=severity,
            response_status=response_status,
        )
    except Exception:
        logger.exception("Fee audit logging failed for action=%s", action)


class SendFeeRemindersView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        days = request.data.get('days', 7)
        try:
            days = int(days)
        except (TypeError, ValueError):
            return Response({"error": "days must be a number"}, status=400)

        if days < 0 or days > 365:
            return Response({"error": "days must be between 0 and 365"}, status=400)

        result = send_due_fee_reminders(days=days, dry_run=False)
        _log_fee_audit(
            "fee_reminders_sent",
            request=request,
            details={"days": days, **result},
            response_status=200,
        )
        return Response({
            "message": "Fee reminders processed successfully.",
            "data": result,
        }, status=200)


# ==========================================
# 1. ADMIN: ASSIGN FEE (Bulk Create)
# ==========================================
class AssignFeeView(APIView):
    """
    POST: Assign fee to a Class. Affects all students in that class.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        serializer = BulkFeeAssignmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=400)
        
        data = serializer.validated_data
        academic_year = data['academic_year']
        class_name = str(data.get('class_name') or '').strip()
        fee_type = _normalize_fee_type(data['fee_type'])
        amount = data['amount']
        installment_count = data['installment_count']
        due_date = data['due_date']
        override_existing = data['override_existing']

        # --- Validate Academic Year (YYYY-YYYY) ---
        if not re.match(r'^\d{4}-\d{4}$', academic_year):
            return Response({
                "error": "Invalid Academic Year format. Use 'YYYY-YYYY' (e.g., 2026-2027)"
            }, status=400)

        # --- Validate Fee Type (No Special Characters) ---
        if not re.match(r'^[a-zA-Z0-9\s-]+$', fee_type):
            return Response({
                "error": "Invalid Fee Type. Only letters, numbers, spaces, and hyphens are allowed."
            }, status=400)

        if _is_hostel_fee_type(fee_type):
            hostel_allocations = _hostel_allocations_for_year(academic_year, class_name or None)
            if not hostel_allocations.exists():
                return Response({
                    "error": "No active hostel allocations found for the selected academic year.",
                    "academic_year": academic_year,
                    "class_name": class_name or None,
                    "fee_type": fee_type,
                }, status=404)

            allocations_by_standard = {}
            skipped_students = []
            for allocation in hostel_allocations:
                standard = _student_standard(allocation.student)
                if not standard:
                    skipped_students.append(allocation.student.student_id)
                    continue
                allocations_by_standard.setdefault(standard.id, {"standard": standard, "students": []})
                allocations_by_standard[standard.id]["students"].append(allocation.student)

            if not allocations_by_standard:
                return Response({
                    "error": "No hostel students with a valid class were found for the selected academic year.",
                    "academic_year": academic_year,
                    "skipped_students": skipped_students,
                }, status=400)

            try:
                with transaction.atomic():
                    count = 0
                    updated = 0
                    affected_classes = []

                    for group in allocations_by_standard.values():
                        standard = group["standard"]
                        affected_classes.append(standard.name)
                        existing_fee_def = FeeDefinition.objects.filter(
                            academic_year=academic_year,
                            standard=standard,
                            fee_type__iexact=fee_type,
                        ).first()

                        if existing_fee_def:
                            paid_students_count = StudentFee.objects.filter(
                                fee_definition=existing_fee_def,
                                payments__payment_status='SUCCESS'
                            ).distinct().count()
                            if paid_students_count > 0:
                                return Response({
                                    "error": "Hostel fee already assigned and one or more students have already paid. Reassignment is not allowed.",
                                    "academic_year": academic_year,
                                    "class_name": standard.name,
                                    "fee_type": fee_type,
                                    "paid_students_count": paid_students_count,
                                }, status=400)

                        fee_def, created = FeeDefinition.objects.update_or_create(
                            academic_year=academic_year,
                            standard=standard,
                            fee_type=fee_type,
                            defaults={
                                'amount': amount,
                                'installment_count': installment_count,
                                'due_date': due_date,
                                'is_active': True
                            }
                        )

                        force_reassign = existing_fee_def is not None
                        unique_students = {student.id: student for student in group["students"]}.values()
                        for stu in unique_students:
                            obj, created_ledger = StudentFee.objects.get_or_create(
                                student=stu,
                                fee_definition=fee_def,
                                defaults={
                                    'total_amount': amount,
                                    'paid_amount': Decimal('0.00'),
                                    'concession_amount': Decimal('0.00')
                                }
                            )

                            if not created_ledger:
                                if override_existing or force_reassign:
                                    obj.total_amount = amount
                                    obj.update_status()
                                    obj.save()
                                    updated += 1
                            else:
                                count += 1

                    _log_fee_audit(
                        action='CREATE',
                        request=request,
                        details={
                            "operation": "assign_hostel_fee",
                            "academic_year": academic_year,
                            "class_name": class_name or "ALL_HOSTEL_CLASSES",
                            "fee_type": fee_type,
                            "students_created": count,
                            "students_updated": updated,
                            "classes_affected": affected_classes,
                            "skipped_students_count": len(skipped_students),
                            "installment_count": installment_count,
                            "override_existing": override_existing,
                        },
                        response_status=201,
                    )
                    return Response({
                        "status": 201,
                        "message": "Hostel fee assigned successfully",
                        "students_created": count,
                        "students_updated": updated,
                        "classes_affected": affected_classes,
                        "skipped_students_count": len(skipped_students),
                        "installment_count": installment_count,
                        "fee_type": fee_type,
                        "academic_year": academic_year
                    }, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"Hostel fee assignment error: {str(e)}")
                _log_fee_audit(
                    action='SYSTEM_ERROR',
                    request=request,
                    details={
                        "operation": "assign_hostel_fee",
                        "error": str(e),
                    },
                    severity='ERROR',
                    response_status=500,
                )
                return Response({
                    "error": "Failed to assign hostel fee",
                    "details": str(e)
                }, status=500)

        if not class_name:
            return Response({"error": "Class is required"}, status=400)

        try:
            standard = Standard.objects.get(name=class_name)
        except Standard.DoesNotExist:
            return Response({"error": "Class not found"}, status=404)

        try:
            with transaction.atomic():
                existing_fee_def = FeeDefinition.objects.filter(
                    academic_year=academic_year,
                    standard=standard,
                    fee_type=fee_type,
                ).first()

                # If fee already assigned and at least one successful payment exists, block reassignment.
                if existing_fee_def:
                    paid_students_count = StudentFee.objects.filter(
                        fee_definition=existing_fee_def,
                        payments__payment_status='SUCCESS'
                    ).distinct().count()
                    if paid_students_count > 0:
                        return Response({
                            "error": "Fee already assigned and one or more students have already paid. Reassignment is not allowed.",
                            "academic_year": academic_year,
                            "class_name": class_name,
                            "fee_type": fee_type,
                            "paid_students_count": paid_students_count,
                        }, status=400)

                # 1. Create/Update the Fee Definition Rule
                fee_def, created = FeeDefinition.objects.update_or_create(
                    academic_year=academic_year,
                    standard=standard,
                    fee_type=fee_type,
                    defaults={
                        'amount': amount,
                        'installment_count': installment_count,
                        'due_date': due_date,
                        'is_active': True
                    }
                )

                # 2. Assign to All Students in that Class
                students = Student.objects.filter(section__standard=standard)
                count = 0
                updated = 0
                # Existing fee structure with no payments must be reassigned for all ledgers.
                force_reassign = existing_fee_def is not None

                for stu in students:
                    obj, created_ledger = StudentFee.objects.get_or_create(
                        student=stu,
                        fee_definition=fee_def,
                        defaults={
                            'total_amount': amount,
                            'paid_amount': Decimal('0.00'),
                            'concession_amount': Decimal('0.00')
                        }
                    )
                    
                    if not created_ledger:
                        if override_existing or force_reassign:
                            obj.total_amount = amount
                            obj.update_status()
                            obj.save()
                            updated += 1
                    else:
                        count += 1

                _log_fee_audit(
                    action='CREATE',
                    request=request,
                    details={
                        "operation": "assign_fee",
                        "academic_year": academic_year,
                        "class_name": class_name,
                        "fee_type": fee_type,
                        "students_created": count,
                        "students_updated": updated,
                        "installment_count": installment_count,
                        "override_existing": override_existing,
                    },
                    response_status=201,
                )
                return Response({
                    "status": 201,
                    "message": "Fee assigned successfully",
                    "students_created": count,
                    "students_updated": updated,
                    "installment_count": installment_count,
                    "class": class_name,
                    "fee_type": fee_type,
                    "academic_year": academic_year
                }, status=status.HTTP_201_CREATED)
                
        except Exception as e:
            logger.error(f"Fee assignment error: {str(e)}")
            _log_fee_audit(
                action='SYSTEM_ERROR',
                request=request,
                details={
                    "operation": "assign_fee",
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )
            return Response({
                "error": "Failed to assign fee",
                "details": str(e)
            }, status=500)


# ==========================================
# 2. STUDENT: INITIATE PAYMENT (Razorpay Order)
# ==========================================
class InitiatePaymentView(APIView):
    """
    POST: Create Razorpay order for fee payment
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentInitiationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=400)
        
        data = serializer.validated_data
        s_id = data['student_id']
        class_name = data['class_name']
        academic_year = data['academic_year']
        fee_type = _normalize_fee_type(data['fee_type'])
        amount = data['amount']
        payment_mode = data['payment_mode']
        return_url = data.get('return_url')

        # --- SECURITY CHECK ---
        user = request.user
        if hasattr(user, 'student_profile'):
            if user.student_profile.student_id != s_id:
                logger.warning(f"Unauthorized payment attempt by user {user.id} for student {s_id}")
                _log_fee_audit(
                    action='PERMISSION_CHANGE',
                    request=request,
                    details={
                        "operation": "initiate_payment",
                        "student_id": s_id,
                        "reason": "student attempted payment for another profile",
                    },
                    severity='SECURITY',
                    response_status=403,
                )
                return Response({
                    "error": "Security Alert: Unauthorized payment attempt."
                }, status=403)

        try:
            # Get student and fee record
            student = Student.objects.get(student_id=s_id)
            if _is_hostel_fee_type(fee_type):
                ledger, hostel_error = _resolve_hostel_ledger(student, academic_year, class_name)
                if hostel_error:
                    return Response(hostel_error, status=404)
            else:
                if not class_name:
                    return Response({"error": "Class is required"}, status=400)
                standard = Standard.objects.get(name=class_name)
                fee_def, fee_def_error = _resolve_active_fee_definition(
                    standard=standard,
                    fee_type=fee_type,
                    academic_year=academic_year,
                )
                if fee_def_error:
                    return Response(fee_def_error, status=404)
                ledger = StudentFee.objects.get(student=student, fee_definition=fee_def)
            fee_def = ledger.fee_definition
            
        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)
        except Standard.DoesNotExist:
            return Response({"error": "Class not found"}, status=404)
        except FeeDefinition.DoesNotExist:
            return Response({"error": "Fee definition not found"}, status=404)
        except StudentFee.DoesNotExist:
            return Response({"error": "Fee record not found for student"}, status=404)

        # Validate amount
        if amount <= 0:
            return Response({"error": "Amount must be greater than 0"}, status=400)
        if amount < Decimal('10.00'):
            return Response({"error": "Minimum payment amount is ₹10"}, status=400)
        
        if amount > ledger.due_amount:
            return Response({
                "error": f"Amount exceeds due amount. Due: {ledger.due_amount}"
            }, status=400)

        installment_error = _validate_installment_payment_amount(ledger, amount)
        if installment_error:
            return Response(installment_error, status=400)

        installment_context = _installment_payment_context(ledger)

        # For Razorpay payments, create order
        if payment_mode == 'RAZORPAY':
            # Razorpay receipt must be <= 40 chars.
            receipt_id = f"fee_{uuid.uuid4().hex[:20]}"
            notes = {
                'student_id': s_id,
                'student_name': student.student_name,
                'class': class_name,
                'fee_type': fee_type,
                'academic_year': fee_def.academic_year
            }
            
            order_result = razorpay.create_order(amount, receipt_id, notes)
            
            if not order_result['success']:
                _log_fee_audit(
                    action='PAYMENT_FAILED',
                    request=request,
                    details={
                        "operation": "initiate_payment",
                        "student_id": s_id,
                        "class_name": class_name,
                        "academic_year": academic_year,
                        "fee_type": fee_type,
                        "amount": str(amount),
                        "payment_mode": payment_mode,
                        "error": str(order_result.get('error')),
                    },
                    severity='ERROR',
                    response_status=order_result.get('status_code', 500),
                )
                return Response({
                    "error": "Failed to create payment order",
                    "details": order_result.get('error')
                }, status=order_result.get('status_code', 500))
            
            # Create pending payment record
            payment = FeePayment.objects.create(
                student_fee=ledger,
                amount_paid=amount,
                installment_number=_payment_installment_number(ledger, amount),
                payment_mode='RAZORPAY',
                transaction_id=FeePayment.generate_transaction_id(),
                razorpay_order_id=order_result['order_data']['id'],
                payment_status='INITIATED',
                remarks="Payment initiated via Razorpay",
                created_by=user if user.is_staff else None
            )
            
            response_data = {
                "status": 200,
                "payment_id": payment.id,
                "order_id": order_result['order_data']['id'],
                "amount": amount,
                "installment_number": installment_context["next_installment_number"],
                "installment_count": installment_context["installment_count"],
                "next_installment_amount": installment_context["next_installment_amount"],
                "currency": "INR",
                "key_id": razorpay.key_id,
                "student_name": student.student_name,
                "student_email": student.student_email if hasattr(student, 'student_email') else '',
                "student_phone": _student_primary_phone(student),
                "description": f"Fee payment for {fee_type} - {class_name}",
                "gateway_mode": "DUMMY" if order_result.get('dummy_mode') else "REAL"
            }
            
            if return_url:
                response_data['return_url'] = return_url

            _log_fee_audit(
                action='PAYMENT_INITIATED',
                request=request,
                details={
                    "operation": "initiate_payment",
                    "student_id": s_id,
                    "class_name": class_name,
                    "academic_year": academic_year,
                    "fee_type": fee_type,
                    "amount": str(amount),
                    "payment_mode": payment_mode,
                    "order_id": order_result['order_data']['id'],
                    "gateway_mode": "DUMMY" if order_result.get('dummy_mode') else "REAL",
                },
                response_status=200,
            )
            
            return Response(response_data)
        
        else:
            # For offline payments, just return payment initiation info
            _log_fee_audit(
                action='PAYMENT_INITIATED',
                request=request,
                details={
                    "operation": "initiate_payment",
                    "student_id": s_id,
                    "class_name": class_name,
                    "academic_year": academic_year,
                    "fee_type": fee_type,
                    "amount": str(amount),
                    "payment_mode": payment_mode,
                    "gateway_mode": "OFFLINE",
                },
                response_status=200,
            )
            return Response({
                "status": 200,
                "message": "Ready for offline payment",
                "student": student.student_name,
                "amount": amount,
                "due_amount": ledger.due_amount,
                "installment_number": installment_context["next_installment_number"],
                "installment_count": installment_context["installment_count"],
                "next_installment_amount": installment_context["next_installment_amount"],
                "payment_mode": payment_mode,
                "requires_verification": False
            })


# ==========================================
# 3. VERIFY RAZORPAY PAYMENT
# ==========================================
class VerifyPaymentView(APIView):
    """
    POST: Verify Razorpay payment after successful transaction
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=400)
        
        data = serializer.validated_data
        order_id = data['razorpay_order_id']
        payment_id = data['razorpay_payment_id']
        signature = data['razorpay_signature']

        try:
            user = request.user
            with transaction.atomic():
                payment = FeePayment.objects.select_for_update().select_related(
                    'student_fee', 'student_fee__student', 'student_fee__fee_definition'
                ).get(razorpay_order_id=order_id)

                if hasattr(user, 'student_profile'):
                    if payment.student_fee.student.student_id != user.student_profile.student_id:
                        _log_fee_audit(
                            action='PERMISSION_CHANGE',
                            request=request,
                            details={
                                "operation": "verify_payment",
                                "order_id": order_id,
                                "reason": "student attempted to verify another student's payment",
                            },
                            severity='SECURITY',
                            response_status=403,
                        )
                        return Response({"error": "Access denied"}, status=403)

                # Idempotent response for already processed payments.
                if payment.payment_status == 'SUCCESS':
                    receipt_serializer = PaymentReceiptSerializer(payment)
                    _log_fee_audit(
                        action='PAYMENT_VERIFIED',
                        request=request,
                        details={
                            "operation": "verify_payment",
                            "order_id": order_id,
                            "payment_id": payment_id,
                            "status": "already_verified",
                        },
                        response_status=200,
                    )
                    _log_fee_audit(
                        action='PAYMENT_PROCESSED',
                        request=request,
                        details={
                            "operation": "fee_payment_confirmed",
                            "source": "verify_api",
                            "order_id": order_id,
                            "payment_id": payment.razorpay_payment_id or payment_id,
                            "transaction_id": payment.transaction_id,
                            "amount": str(payment.amount_paid),
                            "payment_mode": payment.payment_mode,
                            "academic_year": payment.student_fee.fee_definition.academic_year,
                            "status": "already_processed",
                        },
                        response_status=200,
                    )
                    return Response({
                        "status": 200,
                        "message": "Payment already verified",
                        "receipt": receipt_serializer.data,
                        "gateway_mode": "DUMMY" if str(payment.razorpay_order_id).startswith('dummy_order_') else "REAL"
                    })

                if payment.payment_status not in ['INITIATED', 'PENDING']:
                    _log_fee_audit(
                        action='PAYMENT_FAILED',
                        request=request,
                        details={
                            "operation": "verify_payment",
                            "order_id": order_id,
                            "payment_id": payment_id,
                            "reason": f"invalid_status_{payment.payment_status}",
                        },
                        severity='WARNING',
                        response_status=400,
                    )
                    return Response({
                        "error": f"Payment cannot be verified from status '{payment.payment_status}'"
                    }, status=400)

                verification = razorpay.verify_payment(order_id, payment_id, signature)
                if not verification['success']:
                    payment.payment_status = 'FAILED'
                    payment.remarks = f"Verification failed: {verification.get('error')}"
                    payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                    _log_fee_audit(
                        action='PAYMENT_FAILED',
                        request=request,
                        details={
                            "operation": "verify_payment",
                            "order_id": order_id,
                            "payment_id": payment_id,
                            "reason": str(verification.get('error')),
                        },
                        severity='ERROR',
                        response_status=400,
                    )
                    return Response({
                        "error": "Payment verification failed",
                        "details": verification.get('error')
                    }, status=400)

                payment_details = razorpay.fetch_payment(payment_id)
                if not payment_details.get('success') and not verification.get('dummy_mode'):
                    payment.payment_status = 'FAILED'
                    payment.remarks = f"Verification failed: {payment_details.get('error', 'unable to fetch payment details')}"
                    payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                    _log_fee_audit(
                        action='PAYMENT_FAILED',
                        request=request,
                        details={
                            "operation": "verify_payment",
                            "order_id": order_id,
                            "payment_id": payment_id,
                            "reason": str(payment_details.get('error', 'unable to fetch payment details')),
                        },
                        severity='ERROR',
                        response_status=400,
                    )
                    return Response({"error": "Payment verification failed"}, status=400)

                if payment_details['success'] and not payment_details.get('dummy_mode'):
                    payment_data = payment_details.get('payment_data', {})
                    expected_amount_paise = int(payment.amount_paid * 100)

                    if payment_data.get('order_id') != order_id:
                        payment.payment_status = 'FAILED'
                        payment.remarks = "Verification failed: Razorpay order mismatch"
                        payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                        _log_fee_audit(
                            action='PAYMENT_FAILED',
                            request=request,
                            details={
                                "operation": "verify_payment",
                                "order_id": order_id,
                                "payment_id": payment_id,
                                "reason": "razorpay_order_mismatch",
                            },
                            severity='ERROR',
                            response_status=400,
                        )
                        return Response({"error": "Payment verification failed"}, status=400)

                    if int(payment_data.get('amount', 0)) != expected_amount_paise:
                        payment.payment_status = 'FAILED'
                        payment.remarks = "Verification failed: amount mismatch"
                        payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                        _log_fee_audit(
                            action='PAYMENT_FAILED',
                            request=request,
                            details={
                                "operation": "verify_payment",
                                "order_id": order_id,
                                "payment_id": payment_id,
                                "reason": "amount_mismatch",
                            },
                            severity='ERROR',
                            response_status=400,
                        )
                        return Response({"error": "Payment verification failed"}, status=400)

                    if payment_data.get('status') not in ['authorized', 'captured']:
                        payment.payment_status = 'FAILED'
                        payment.remarks = f"Verification failed: invalid Razorpay status '{payment_data.get('status')}'"
                        payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                        _log_fee_audit(
                            action='PAYMENT_FAILED',
                            request=request,
                            details={
                                "operation": "verify_payment",
                                "order_id": order_id,
                                "payment_id": payment_id,
                                "reason": f"invalid_gateway_status_{payment_data.get('status')}",
                            },
                            severity='ERROR',
                            response_status=400,
                        )
                        return Response({"error": "Payment verification failed"}, status=400)

                    payment.remarks = f"Payment method: {payment_data.get('method', 'unknown')}"

                payment.razorpay_payment_id = payment_id
                payment.razorpay_signature = signature
                payment.payment_status = 'SUCCESS'
                payment.transaction_id = payment_id
                payment.payment_date = timezone.now().date()
                payment.save()

                ledger = payment.student_fee
                ledger.paid_amount = ledger.payments.filter(
                    payment_status='SUCCESS'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
                ledger.last_payment_date = payment.payment_date
                ledger.update_status()
                ledger.save()

                receipt_serializer = PaymentReceiptSerializer(payment)
                _log_fee_audit(
                    action='PAYMENT_VERIFIED',
                    request=request,
                    details={
                        "operation": "verify_payment",
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "transaction_id": payment.transaction_id,
                        "amount": str(payment.amount_paid),
                        "gateway_mode": "DUMMY" if verification.get('dummy_mode') else "REAL",
                    },
                    response_status=200,
                )
                _log_fee_audit(
                    action='PAYMENT_PROCESSED',
                    request=request,
                    details={
                        "operation": "fee_payment_confirmed",
                        "source": "verify_api",
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "transaction_id": payment.transaction_id,
                        "amount": str(payment.amount_paid),
                        "payment_mode": payment.payment_mode,
                        "academic_year": payment.student_fee.fee_definition.academic_year,
                        "gateway_mode": "DUMMY" if verification.get('dummy_mode') else "REAL",
                        "status": "processed",
                    },
                    response_status=200,
                )
                return Response({
                    "status": 200,
                    "message": "Payment verified and recorded successfully",
                    "receipt": receipt_serializer.data,
                    "gateway_mode": "DUMMY" if verification.get('dummy_mode') else "REAL"
                })

        except FeePayment.DoesNotExist:
            _log_fee_audit(
                action='PAYMENT_FAILED',
                request=request,
                details={
                    "operation": "verify_payment",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "reason": "order_not_found",
                },
                severity='WARNING',
                response_status=404,
            )
            return Response({
                "error": "Invalid order ID. No matching payment found."
            }, status=404)
        except Exception as e:
            logger.error(f"Payment verification error: {str(e)}")
            _log_fee_audit(
                action='SYSTEM_ERROR',
                request=request,
                details={
                    "operation": "verify_payment",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )
            return Response({
                "error": "Payment verification failed",
                "details": str(e)
            }, status=500)


# ==========================================
# 3B. CANCEL RAZORPAY PAYMENT (Checkout Dismissed)
# ==========================================
class CancelPaymentView(APIView):
    """
    POST: Mark INITIATED/PENDING payment as FAILED when user closes checkout
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order_id = request.data.get('razorpay_order_id')
        reason = request.data.get('reason', 'Payment cancelled by user')

        if not order_id:
            return Response({"error": "razorpay_order_id is required"}, status=400)

        try:
            with transaction.atomic():
                payment = FeePayment.objects.select_for_update().select_related(
                    'student_fee', 'student_fee__student', 'student_fee__fee_definition'
                ).get(razorpay_order_id=order_id)

                user = request.user
                if hasattr(user, 'student_profile'):
                    if payment.student_fee.student.student_id != user.student_profile.student_id:
                        _log_fee_audit(
                            action='PERMISSION_CHANGE',
                            request=request,
                            details={
                                "operation": "cancel_payment",
                                "order_id": order_id,
                                "reason": "student attempted to cancel another student's payment",
                            },
                            severity='SECURITY',
                            response_status=403,
                        )
                        return Response({"error": "Access denied"}, status=403)

                if payment.payment_status == 'SUCCESS':
                    return Response({
                        "status": 200,
                        "message": "Payment already completed"
                    })

                if payment.payment_status in ['FAILED', 'REFUNDED', 'REFUND_PENDING']:
                    return Response({
                        "status": 200,
                        "message": "Payment already closed"
                    })

                payment.payment_status = 'FAILED'
                payment.remarks = reason
                payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])

                _log_fee_audit(
                    action='PAYMENT_FAILED',
                    request=request,
                    details={
                        "operation": "payment_cancelled",
                        "source": "checkout_dismiss",
                        "order_id": order_id,
                        "payment_id": payment.razorpay_payment_id,
                        "transaction_id": payment.transaction_id,
                        "amount": str(payment.amount_paid),
                        "academic_year": payment.student_fee.fee_definition.academic_year,
                        "reason": reason,
                    },
                    severity='WARNING',
                    response_status=200,
                )

                return Response({
                    "status": 200,
                    "message": "Payment cancellation recorded"
                })

        except FeePayment.DoesNotExist:
            return Response({"error": "Invalid order ID. No matching payment found."}, status=404)
        except Exception as e:
            logger.error(f"Payment cancellation error: {str(e)}")
            _log_fee_audit(
                action='SYSTEM_ERROR',
                request=request,
                details={
                    "operation": "cancel_payment",
                    "order_id": order_id,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )
            return Response({"error": "Failed to cancel payment"}, status=500)


# ==========================================
# 4. RECORD OFFLINE PAYMENT (Cash/Cheque/etc)
# ==========================================
class RecordOfflinePaymentView(APIView):
    """
    POST: Record offline payment (Cash, Cheque, DD)
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        s_id = request.data.get('student_id')
        class_name = request.data.get('class_name')
        academic_year = request.data.get('academic_year')
        fee_type = _normalize_fee_type(request.data.get('fee_type'))
        
        try:
            amount_paying = Decimal(str(request.data.get('paid_amount')))
        except (TypeError, ValueError, InvalidOperation):
            return Response({"error": "Invalid amount format"}, status=400)

        mode = request.data.get('payment_mode')
        txn_id = request.data.get('transaction_id', '')
        remarks = request.data.get('remarks', '')

        if not all([s_id, academic_year, fee_type, amount_paying, mode]):
            return Response({
                "error": "Missing required fields: student_id, academic_year, fee_type, paid_amount, payment_mode"
            }, status=400)

        # Validate payment mode
        valid_modes = ['CASH', 'CHEQUE', 'DD', 'ONLINE']
        if mode not in valid_modes:
            return Response({
                "error": f"Invalid payment mode. Choose from: {', '.join(valid_modes)}"
            }, status=400)

        # Offline collections are restricted to admin and allowed staff roles.
        user = request.user
        staff_role = getattr(getattr(user, 'staff_profile', None), 'role', None)
        has_allowed_staff_role = user.user_type == 'staff' and staff_role in {'admin_staff', 'finance_staff'}
        if not (user.is_superuser or user.user_type in ('admin', 'super_admin') or has_allowed_staff_role):
            _log_fee_audit(
                action='PERMISSION_CHANGE',
                request=request,
                details={
                    "operation": "record_offline_payment",
                    "student_id": s_id,
                    "class_name": class_name,
                    "academic_year": academic_year,
                    "fee_type": fee_type,
                    "reason": "insufficient_role_for_offline_payment",
                },
                severity='SECURITY',
                response_status=403,
            )
            return Response({"error": "Unauthorized"}, status=403)

        try:
            with transaction.atomic():
                student = Student.objects.get(student_id=s_id)
                if _is_hostel_fee_type(fee_type):
                    ledger, hostel_error = _resolve_hostel_ledger(student, academic_year, class_name or '')
                    if hostel_error:
                        return Response(hostel_error, status=404)
                else:
                    if not class_name:
                        return Response({"error": "Class is required"}, status=400)
                    standard = Standard.objects.get(name=class_name)
                    fee_def, fee_def_error = _resolve_active_fee_definition(
                        standard=standard,
                        fee_type=fee_type,
                        academic_year=academic_year,
                    )
                    if fee_def_error:
                        return Response(fee_def_error, status=404)
                    ledger = StudentFee.objects.get(student=student, fee_definition=fee_def)
                installment_context = _installment_payment_context(ledger)
                
        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)
        except Standard.DoesNotExist:
            return Response({"error": "Class not found"}, status=404)
        except FeeDefinition.DoesNotExist:
            return Response({"error": "Fee definition not found"}, status=404)
        except StudentFee.DoesNotExist:
            return Response({"error": "Fee record not found for student"}, status=404)

        # Validate amount
        if amount_paying <= 0:
            return Response({"error": "Amount must be greater than 0"}, status=400)
        
        if amount_paying > ledger.due_amount:
            return Response({
                "error": f"Amount exceeds due amount. Due: {ledger.due_amount}"
            }, status=400)

        installment_error = _validate_installment_payment_amount(ledger, amount_paying)
        if installment_error:
            return Response(installment_error, status=400)

        # Check for duplicate transaction ID if provided
        if txn_id and FeePayment.objects.filter(transaction_id=txn_id).exists():
            return Response({
                "error": "Transaction ID already exists"
            }, status=400)

        with transaction.atomic():
            locked_ledger = StudentFee.objects.select_for_update().get(id=ledger.id)
            if amount_paying > locked_ledger.due_amount:
                return Response({
                    "error": f"Amount exceeds due amount. Due: {locked_ledger.due_amount}"
                }, status=400)
            locked_installment_error = _validate_installment_payment_amount(locked_ledger, amount_paying)
            if locked_installment_error:
                return Response(locked_installment_error, status=400)
            locked_installment_context = _installment_payment_context(locked_ledger)

            payment = FeePayment.objects.create(
                student_fee=locked_ledger,
                amount_paid=amount_paying,
                installment_number=_payment_installment_number(locked_ledger, amount_paying),
                payment_mode=mode,
                transaction_id=txn_id or FeePayment.generate_transaction_id(),
                payment_status='SUCCESS',
                remarks=remarks,
                created_by=user if user.is_staff else None
            )

            locked_ledger.paid_amount += amount_paying
            locked_ledger.last_payment_date = payment.payment_date
            locked_ledger.update_status()
            locked_ledger.save()

        # Generate receipt
        receipt_serializer = PaymentReceiptSerializer(payment)
        _log_fee_audit(
            action='PAYMENT_PROCESSED',
            request=request,
            details={
                "operation": "record_offline_payment",
                "student_id": s_id,
                "class_name": class_name,
                "academic_year": academic_year,
                "fee_type": fee_type,
                "payment_mode": mode,
                "transaction_id": payment.transaction_id,
                "amount": str(amount_paying),
            },
            response_status=201,
        )

        return Response({
            "status": 201,
            "message": "Payment recorded successfully",
            "receipt": receipt_serializer.data
        }, status=status.HTTP_201_CREATED)


# ==========================================
# 5. GET PAYMENT RECEIPT
# ==========================================
class ReceiptView(APIView):
    """
    GET: Get payment receipt by transaction ID or payment ID
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        txn_id = request.query_params.get('transaction_id')
        payment_id = request.query_params.get('payment_id')
        
        if not txn_id and not payment_id:
            return Response({
                "error": "Provide either transaction_id or payment_id"
            }, status=400)

        try:
            if txn_id:
                payment = FeePayment.objects.get(transaction_id=txn_id)
            else:
                payment = FeePayment.objects.get(id=payment_id)
                
        except FeePayment.DoesNotExist:
            return Response({"error": "Receipt not found"}, status=404)

        # Security check - students can only view their own receipts
        user = request.user
        if hasattr(user, 'student_profile'):
            if user.student_profile.student_id != payment.student_fee.student.student_id:
                return Response({"error": "Access denied"}, status=403)

        serializer = PaymentReceiptSerializer(payment)
        return Response({
            "status": 200,
            "receipt": serializer.data
        })


# ==========================================
# 6. GET STUDENT FEE SUMMARY
# ==========================================
class StudentFeeSummaryView(APIView):
    """
    GET: Get complete fee summary for a student
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        s_id = request.query_params.get('student_id')
        academic_year = request.query_params.get('academic_year')

        if not s_id:
            return Response({"error": "student_id is required"}, status=400)

        # Security check
        user = request.user
        if hasattr(user, 'student_profile'):
            if user.student_profile.student_id != s_id:
                return Response({"error": "Access denied"}, status=403)

        try:
            student = Student.objects.get(student_id=s_id)
        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)

        profile_image_url = None
        if getattr(student, 'profile_image', None):
            try:
                profile_image_url = request.build_absolute_uri(student.profile_image.url)
            except Exception:
                profile_image_url = student.profile_image.url

        # Get all fee records for student
        fee_records = StudentFee.objects.filter(
            student=student,
            fee_definition__is_active=True
        ).select_related('fee_definition', 'fee_definition__standard')

        if academic_year:
            fee_records = fee_records.filter(fee_definition__academic_year=academic_year)

        summary = []
        total_due = Decimal('0.00')
        total_paid = Decimal('0.00')
        total_concession = Decimal('0.00')

        for record in fee_records:
            payments = record.payments.filter(payment_status='SUCCESS').order_by('-created_at')
            
            record_data = {
                'fee_id': record.id,
                'academic_year': record.fee_definition.academic_year,
                'fee_type': record.fee_definition.fee_type,
                'class': record.fee_definition.standard.name,
                'total_amount': record.total_amount,
                'concession': record.concession_amount,
                'paid_amount': record.paid_amount,
                'due_amount': record.due_amount,
                'status': record.status,
                'installment_count': record.installment_count,
                'installment_amount': record.installment_amount,
                'installments_paid_count': record.installments_paid_count,
                'installments_remaining_count': record.installments_remaining_count,
                'next_installment_number': record.next_installment_number,
                'next_installment_amount': record.next_installment_amount,
                'due_date': record.fee_definition.due_date,
                'last_payment_date': record.last_payment_date,
                'payments': [
                    {
                        'date': p.payment_date,
                        'payment_datetime': timezone.localtime(p.created_at).isoformat(),
                        'amount': p.amount_paid,
                        'installment_number': p.installment_number,
                        'mode': p.get_payment_mode_display(),
                        'transaction_id': p.transaction_id
                    }
                    for p in payments
                ]
            }
            
            summary.append(record_data)
            total_due += record.due_amount
            total_paid += record.paid_amount
            total_concession += record.concession_amount

        return Response({
            "status": 200,
            "student": {
                "id": student.student_id,
                "name": student.student_name,
                "class": student.section.standard.name if student.section else None,
                "section": student.section.name if student.section else None,
                "profile_image": profile_image_url
            },
            "summary": {
                "total_fee": sum(r['total_amount'] for r in summary),
                "total_concession": total_concession,
                "total_paid": total_paid,
                "total_due": total_due,
                "records_count": len(summary),
                "paid_count": sum(1 for r in summary if r['status'] == 'PAID'),
                "unpaid_count": sum(1 for r in summary if r['status'] in ['UNPAID', 'PARTIAL', 'OVERDUE'])
            },
            "fee_records": summary
        })


# ==========================================
# 7. ADMIN: VIEW FEE STRUCTURE
# ==========================================
class ViewFeeStructureView(APIView):
    """
    GET: View all fees set by Admin.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        definitions = FeeDefinition.objects.filter(is_active=True).order_by(
            '-academic_year', 'standard__name', 'fee_type'
        )

        # Apply filters
        year = request.query_params.get('academic_year')
        f_type = request.query_params.get('fee_type')
        class_name = request.query_params.get('class')
        search_text = (request.query_params.get('q') or '').strip()

        if year:
            definitions = definitions.filter(academic_year=year)
        if f_type:
            definitions = definitions.filter(fee_type__iexact=f_type)
        if class_name:
            definitions = definitions.filter(standard__name=class_name)
        if search_text:
            definitions = definitions.filter(
                Q(fee_type__icontains=search_text) |
                Q(academic_year__icontains=search_text) |
                Q(standard__name__icontains=search_text)
            )

        if not definitions.exists():
            return Response({
                "message": "No fee structures found for these filters",
                "data": [],
                "count": 0,
                "pagination": {
                    "page": 1,
                    "page_size": 0,
                    "total": 0,
                    "total_pages": 1,
                    "has_next": False,
                    "has_previous": False
                }
            }, status=200)

        page_raw = request.query_params.get('page')
        page_size_raw = request.query_params.get('page_size')

        if page_raw is None and page_size_raw is None:
            serializer = FeeDefinitionSerializer(definitions, many=True)
            return Response({
                "status": 200,
                "count": definitions.count(),
                "data": serializer.data
            })

        try:
            page = int(page_raw or 1)
            page_size = int(page_size_raw or 10)
        except (TypeError, ValueError):
            return Response({"error": "Invalid page or page_size"}, status=400)

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        page_size = min(page_size, 100)

        total = definitions.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged = definitions[start:end]
        serializer = FeeDefinitionSerializer(paged, many=True)

        return Response({
            "status": 200,
            "count": total,
            "data": serializer.data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1
            }
        })


# ==========================================
# 8. ADMIN: UPDATE FEE STRUCTURE
# ==========================================
class UpdateFeeStructureView(APIView):
    """
    PUT: Edit fee definition
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def put(self, request):
        fee_id = request.data.get('fee_id')
        new_year = request.data.get('academic_year')
        new_class_name = request.data.get('class_name')
        new_type = request.data.get('fee_type')
        new_amount = request.data.get('new_amount')
        new_installment_count = request.data.get('installment_count')
        new_due_date = request.data.get('due_date')

        if not fee_id:
            return Response({"error": "fee_id is required"}, status=400)

        try:
            fee_def = FeeDefinition.objects.get(id=fee_id)
        except FeeDefinition.DoesNotExist:
            return Response({"error": "Fee Definition not found"}, status=404)

        paid_exists = FeePayment.objects.filter(
            student_fee__fee_definition=fee_def,
            payment_status='SUCCESS'
        ).exists()
        if paid_exists:
            return Response({
                "error": "Cannot update fee structure. Students have already paid this fee."
            }, status=400)

        # Check for duplicate before updating
        target_year = new_year if new_year else fee_def.academic_year
        target_type = new_type if new_type else fee_def.fee_type
        
        target_standard = fee_def.standard
        if new_class_name:
            try:
                target_standard = Standard.objects.get(name=new_class_name)
            except Standard.DoesNotExist:
                return Response({"error": f"Class '{new_class_name}' not found"}, status=404)

        # Check if combination already exists (excluding current)
        is_duplicate = FeeDefinition.objects.filter(
            academic_year=target_year,
            standard=target_standard,
            fee_type=target_type,
            is_active=True
        ).exclude(id=fee_id).exists()

        if is_duplicate:
            return Response({
                "error": f"Conflict: A '{target_type}' for Class '{target_standard.name}' ({target_year}) already exists."
            }, status=400)

        # Handle Class Change
        if new_class_name and new_class_name != fee_def.standard.name:
            has_payments = FeePayment.objects.filter(
                student_fee__fee_definition=fee_def
            ).exists()
            
            if has_payments:
                return Response({
                    "error": "Cannot change Class. Students have already made payments."
                }, status=400)

            # Move to new class
            with transaction.atomic():
                StudentFee.objects.filter(fee_definition=fee_def).delete()
                fee_def.standard = target_standard
                fee_def.save()
                
                # Recreate ledgers for new class
                new_students = Student.objects.filter(section__standard=target_standard)
                for stu in new_students:
                    StudentFee.objects.create(
                        student=stu,
                        fee_definition=fee_def,
                        total_amount=new_amount if new_amount else fee_def.amount
                    )

        # Update other fields
        if new_year:
            fee_def.academic_year = new_year
        if new_type:
            fee_def.fee_type = new_type
        if new_due_date:
            fee_def.due_date = new_due_date
        if new_installment_count not in (None, ''):
            try:
                parsed_installment_count = int(new_installment_count)
            except (TypeError, ValueError):
                return Response({"error": "installment_count must be a valid integer"}, status=400)
            if parsed_installment_count < 1:
                return Response({"error": "installment_count must be at least 1"}, status=400)
            fee_def.installment_count = parsed_installment_count
        
        if new_amount:
            fee_def.amount = Decimal(str(new_amount))
            # Update existing ledgers
            ledgers = StudentFee.objects.filter(fee_definition=fee_def)
            for ledger in ledgers:
                ledger.total_amount = fee_def.amount
                ledger.update_status()
                ledger.save()

        fee_def.save()

        _log_fee_audit(
            action='UPDATE',
            request=request,
            details={
                "operation": "update_fee_structure",
                "fee_id": fee_def.id,
                "class_name": fee_def.standard.name,
                "academic_year": fee_def.academic_year,
                "fee_type": fee_def.fee_type,
                "amount": str(fee_def.amount),
                "installment_count": fee_def.installment_count,
                "due_date": str(fee_def.due_date) if fee_def.due_date else None,
            },
            response_status=200,
        )

        return Response({
            "status": 200,
            "message": "Fee structure updated successfully",
            "data": {
                "id": fee_def.id,
                "class": fee_def.standard.name,
                "academic_year": fee_def.academic_year,
                "fee_type": fee_def.fee_type,
                "amount": fee_def.amount,
                "installment_count": fee_def.installment_count,
                "due_date": fee_def.due_date
            }
        })


# ==========================================
# 9. ADMIN: CLASS FEE REPORT
# ==========================================
class ClassFeeReportView(APIView):
    """
    GET: View payment status of a specific Class & Section.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaffOrHostelWarden]

    def get(self, request):
        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        academic_year = request.query_params.get('academic_year')
        fee_type = request.query_params.get('fee_type')
        status_filter = request.query_params.get('status')  # PAID, UNPAID, OVERDUE
        staff_role = getattr(getattr(request.user, 'staff_profile', None), 'role', None)

        if not academic_year:
            return Response({
                "error": "Param required: academic_year"
            }, status=400)

        normalized_fee_type = _normalize_fee_type(fee_type)
        if staff_role == 'hostel_warden' and normalized_fee_type != HOSTEL_FEE_TYPE:
            return Response({
                "error": "Hostel wardens can only access hostel fee reports."
            }, status=403)

        # Fetch ledgers for the selected academic year and optional filters.
        ledgers = StudentFee.objects.filter(
            fee_definition__academic_year=academic_year,
            fee_definition__is_active=True,
        ).select_related('student', 'student__section', 'student__section__standard', 'fee_definition')

        if class_name:
            ledgers = ledgers.filter(student__section__standard__name=class_name)
        if section_name:
            ledgers = ledgers.filter(student__section__name=section_name)
        if fee_type:
            ledgers = ledgers.filter(fee_definition__fee_type__iexact=normalized_fee_type)

        if status_filter:
            ledgers = ledgers.filter(status=status_filter)

        # Build student-wise aggregation to avoid double-counting when multiple fee records exist per student.
        student_map = {}
        status_priority = {'OVERDUE': 3, 'UNPAID': 2, 'PARTIAL': 1, 'PAID': 0}
        for ledger in ledgers:
            key = ledger.student.student_id
            if key not in student_map:
                student_map[key] = {
                    "student_id": ledger.student.student_id,
                    "student_name": ledger.student.student_name,
                    "student_roll": _student_roll_number(ledger.student),
                    "parent_mobile": _student_primary_phone(ledger.student),
                    "student_class": ledger.student.section.standard.name if ledger.student.section else "",
                    "student_section": ledger.student.section.name if ledger.student.section else "",
                    "total_amount": Decimal('0.00'),
                    "concession_amount": Decimal('0.00'),
                    "effective_total": Decimal('0.00'),
                    "paid_amount": Decimal('0.00'),
                    "due_amount": Decimal('0.00'),
                    "payment_status": ledger.status,
                    "installment_count": ledger.installment_count,
                    "next_installment_amount": ledger.next_installment_amount,
                    "installments_count": 0,
                    "installments_remaining_count": ledger.installments_remaining_count,
                    "last_payment_date": ledger.last_payment_date,
                    "last_payment": None,
                }

            row = student_map[key]
            row["total_amount"] += ledger.total_amount
            row["concession_amount"] += ledger.concession_amount
            row["effective_total"] += ledger.effective_total
            row["paid_amount"] += ledger.paid_amount
            row["due_amount"] += ledger.due_amount
            row["installments_count"] += ledger.payments.count()
            row["installments_remaining_count"] = max(
                row["installments_remaining_count"],
                ledger.installments_remaining_count,
            )
            if row["due_amount"] > Decimal('0.00'):
                row["next_installment_amount"] = ledger.next_installment_amount

            if ledger.last_payment_date and (not row["last_payment_date"] or ledger.last_payment_date > row["last_payment_date"]):
                row["last_payment_date"] = ledger.last_payment_date
                last_payment = ledger.payments.order_by('-payment_date', '-created_at').first()
                if last_payment:
                    row["last_payment"] = {
                        "date": last_payment.payment_date,
                        "amount": last_payment.amount_paid,
                        "mode": last_payment.get_payment_mode_display(),
                        "transaction_id": last_payment.transaction_id,
                    }

            if status_priority.get(ledger.status, 0) > status_priority.get(row["payment_status"], 0):
                row["payment_status"] = ledger.status

        students = list(student_map.values())

        # Calculate student-wise statistics
        total_students = len(students)
        paid_count = sum(1 for s in students if s["payment_status"] == 'PAID')
        unpaid_count = sum(1 for s in students if s["payment_status"] in ['UNPAID', 'PARTIAL'])
        overdue_count = sum(1 for s in students if s["payment_status"] == 'OVERDUE')

        total_collected = sum((s["paid_amount"] for s in students), Decimal('0.00'))
        total_pending = sum((s["due_amount"] for s in students), Decimal('0.00'))
        total_assigned = sum((s["total_amount"] for s in students), Decimal('0.00'))

        # If filter is narrowed to a specific class + fee type, this is meaningful.
        fee_per_student = None
        if class_name and fee_type:
            fee_def = FeeDefinition.objects.filter(
                standard__name=class_name,
                fee_type__iexact=normalized_fee_type,
                academic_year=academic_year,
                is_active=True,
            ).first()
            if fee_def:
                fee_per_student = fee_def.amount

        return Response({
            "status": 200,
            "class": class_name or "ALL",
            "section": section_name or "ALL",
            "fee_type": fee_type or "ALL",
            "academic_year": academic_year,
            "total_fee_per_student": fee_per_student,
            "statistics": {
                "total_students": total_students,
                "paid": paid_count,
                "unpaid": unpaid_count,
                "overdue": overdue_count,
                "collection_rate": round((paid_count / total_students * 100), 2) if total_students > 0 else 0,
                "total_collected": total_collected,
                "total_pending": total_pending,
                "total_assigned": total_assigned,
            },
            "students": students
        })


# ==========================================
# 10. ADMIN: SCHOOL-WIDE DUE REPORT
# ==========================================
class SchoolDueReportView(APIView):
    """
    GET: View pending dues grouped by Class.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaffOrHostelWarden]

    def get(self, request):
        academic_year = request.query_params.get('academic_year')
        fee_type = request.query_params.get('fee_type')
        staff_role = getattr(getattr(request.user, 'staff_profile', None), 'role', None)
        
        if not academic_year:
            return Response({
                "error": "Param 'academic_year' is required"
            }, status=400)

        normalized_fee_type = _normalize_fee_type(fee_type)
        if staff_role == 'hostel_warden' and normalized_fee_type != HOSTEL_FEE_TYPE:
            return Response({
                "error": "Hostel wardens can only access hostel fee reports."
            }, status=403)

        # Base scope for selected academic year and optional fee type.
        all_ledgers = StudentFee.objects.filter(
            fee_definition__academic_year=academic_year,
            fee_definition__is_active=True
        )
        if fee_type:
            all_ledgers = all_ledgers.filter(fee_definition__fee_type__iexact=normalized_fee_type)

        # Filter unpaid / partial / overdue fees for due report rows.
        dues = all_ledgers.exclude(status='PAID').select_related(
            'student', 'student__section', 'student__section__standard'
        )

        # Calculate Global Summary
        total_students_count = all_ledgers.values('student_id').distinct().count()
        due_students_count = dues.values('student_id').distinct().count()
        total_due_amt = 0
        
        # Grouping Logic
        grouped_data = {}

        for item in dues:
            class_name = item.student.section.standard.name
            pending = item.due_amount
            total_due_amt += pending

            if class_name not in grouped_data:
                grouped_data[class_name] = {}

            student_key = item.student.student_id
            if student_key not in grouped_data[class_name]:
                grouped_data[class_name][student_key] = {
                    "student_id": item.student.student_id,
                    "student_name": item.student.student_name,
                    "roll_number": _student_roll_number(item.student),
                    "section": item.student.section.name,
                    "parent_phone": _student_primary_phone(item.student),
                    "due_amount": Decimal('0.00'),
                    "paid_amount": Decimal('0.00'),
                    "total_amount": Decimal('0.00'),
                    "concession": Decimal('0.00'),
                    "installments_paid": 0,
                    "last_payment_date": item.last_payment_date,
                    "status": item.status
                }

            existing = grouped_data[class_name][student_key]
            existing["due_amount"] += pending
            existing["paid_amount"] += item.paid_amount
            existing["total_amount"] += item.total_amount
            existing["concession"] += item.concession_amount
            existing["installments_paid"] += item.payments.filter(payment_status='SUCCESS').count()
            if item.last_payment_date and (
                not existing["last_payment_date"] or item.last_payment_date > existing["last_payment_date"]
            ):
                existing["last_payment_date"] = item.last_payment_date

            status_priority = {'OVERDUE': 3, 'UNPAID': 2, 'PARTIAL': 1, 'PAID': 0}
            if status_priority.get(item.status, 0) > status_priority.get(existing["status"], 0):
                existing["status"] = item.status

        # Sorting Logic (Class 1, 2, ... 10, LKG, UKG)
        def sort_key(key):
            if key.isdigit():
                return (0, int(key))  # Numeric classes first
            return (1, key)  # Non-numeric (LKG, UKG) after

        sorted_classes = sorted(grouped_data.keys(), key=sort_key)

        # Construct Final Sorted List
        final_results = []
        for c_name in sorted_classes:
            class_students = list(grouped_data[c_name].values())
            final_results.append({
                "class": c_name,
                "total_due_students": len(class_students),
                "total_due_amount": sum((s['due_amount'] for s in class_students), Decimal('0.00')),
                "students": class_students
            })

        return Response({
            "status": 200,
            "academic_year": academic_year,
            "fee_type": fee_type or "ALL",
            "summary": {
                "total_students_in_school": total_students_count,
                "due_students_count": due_students_count,
                "total_pending_amount": total_due_amt,
                "coverage_percentage": round(
                    (due_students_count / total_students_count * 100), 2
                ) if total_students_count > 0 else 0
            },
            "dues_by_class": final_results
        })


# ==========================================
# 11. ADMIN: DAILY COLLECTION REPORT
# ==========================================
class DailyCollectionReportView(APIView):
    """
    GET: Total payments received on a specific date or date range.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        date_str = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        payments = FeePayment.objects.filter(payment_status='SUCCESS')
        
        if date_str:
            # Single date
            payments = payments.filter(payment_date=date_str)
        elif from_date and to_date:
            # Date range
            payments = payments.filter(
                payment_date__gte=from_date,
                payment_date__lte=to_date
            )
        else:
            # Default to today
            payments = payments.filter(payment_date=timezone.now().date())
        
        total_collected = payments.aggregate(Sum('amount_paid'))['amount_paid__sum'] or 0
        
        # Breakdown by Payment Mode
        modes = {}
        class_wise = {}
        txn_list = []
        
        for p in payments.select_related('student_fee__student', 'student_fee__student__section__standard'):
            mode = p.payment_mode
            if mode not in modes:
                modes[mode] = 0
            modes[mode] += p.amount_paid
            
            # Class-wise breakdown
            class_name = p.student_fee.student.section.standard.name
            if class_name not in class_wise:
                class_wise[class_name] = 0
            class_wise[class_name] += p.amount_paid
            
            txn_list.append({
                "student_id": p.student_fee.student.student_id,
                "student_name": p.student_fee.student.student_name,
                "class": class_name,
                "section": p.student_fee.student.section.name,
                "fee_type": p.student_fee.fee_definition.fee_type,
                "amount": p.amount_paid,
                "mode": p.get_payment_mode_display(),
                "txn_id": p.transaction_id,
                "date": p.payment_date
            })

        return Response({
            "status": 200,
            "date_range": {
                "from": date_str or from_date or timezone.now().date(),
                "to": date_str or to_date or timezone.now().date()
            },
            "total_collected": total_collected,
            "total_transactions": payments.count(),
            "mode_breakdown": modes,
            "class_wise_breakdown": class_wise,
            "transactions": txn_list
        })


# ==========================================
# 12. ADMIN: FEES STATS CARDS
# ==========================================
class AdminFeeStatsCardsView(APIView):
    """Accurate metrics for fees dashboard cards."""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        academic_year = request.query_params.get('academic_year')
        today = timezone.localdate()

        def _to_float(value):
            return round(float(value or 0), 2)

        def _pct(part, whole):
            whole_num = float(whole or 0)
            if whole_num <= 0:
                return 0.0
            return round((float(part or 0) / whole_num) * 100, 2)

        due_expression = ExpressionWrapper(
            F('total_amount') - F('paid_amount') - F('concession_amount'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )
        pending_expression = Greatest(
            due_expression,
            Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))
        )

        fee_defs = FeeDefinition.objects.filter(is_active=True).select_related('standard')
        ledgers = StudentFee.objects.filter(
            fee_definition__is_active=True
        ).select_related('student', 'fee_definition__standard')
        payments = FeePayment.objects.filter(
            payment_status='SUCCESS',
            student_fee__fee_definition__is_active=True
        )

        if academic_year:
            fee_defs = fee_defs.filter(academic_year=academic_year)
            ledgers = ledgers.filter(fee_definition__academic_year=academic_year)
            payments = payments.filter(student_fee__fee_definition__academic_year=academic_year)

        aggregates = ledgers.aggregate(
            total_assigned=Sum('total_amount'),
            total_collected=Sum('paid_amount'),
            total_concession=Sum('concession_amount'),
            total_pending=Sum(pending_expression),
        )

        total_assigned = aggregates['total_assigned'] or Decimal('0.00')
        total_collected = aggregates['total_collected'] or Decimal('0.00')
        total_concession = aggregates['total_concession'] or Decimal('0.00')
        total_pending = aggregates['total_pending'] or Decimal('0.00')
        effective_total = total_assigned - total_concession

        due_ledgers = ledgers.filter(status__in=['UNPAID', 'PARTIAL', 'OVERDUE'])
        due_students_count = due_ledgers.values('student_id').distinct().count()
        total_students_count = ledgers.values('student_id').distinct().count()

        today_payments = payments.filter(payment_date=today)
        today_collected = today_payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        today_transactions = today_payments.count()

        status_counts = ledgers.values('status').annotate(count=Count('id'))
        status_map = {row['status']: row['count'] for row in status_counts}
        paid_students_count = ledgers.filter(status='PAID').values('student_id').distinct().count()

        return Response({
            "status": 200,
            "data": {
                "filters": {
                    "academic_year": academic_year or "ALL",
                    "as_of_date": str(today),
                },
                "cards": {
                    "total_fee_defined": _to_float(fee_defs.aggregate(total=Sum('amount'))['total']),
                    "total_assigned_amount": _to_float(total_assigned),
                    "total_fee_structures": fee_defs.count(),
                    "assigned_fee_records_count": ledgers.count(),
                    "active_classes_count": fee_defs.values('standard_id').distinct().count(),
                    "total_students_count": total_students_count,
                    "paid_students_count": paid_students_count,
                    "due_students_count": due_students_count,
                    "total_pending_amount": _to_float(total_pending),
                    "total_collected_amount": _to_float(total_collected),
                    "total_concession_amount": _to_float(total_concession),
                    "today_collected_amount": _to_float(today_collected),
                    "today_transactions": today_transactions,
                    "collection_rate": _pct(total_collected, effective_total),
                },
                "status_counts": {
                    "paid": status_map.get('PAID', 0),
                    "unpaid": status_map.get('UNPAID', 0) + status_map.get('PARTIAL', 0),
                    "overdue": status_map.get('OVERDUE', 0),
                    "refunded": status_map.get('REFUNDED', 0),
                }
            }
        }, status=200)


# ==========================================
# 13. ADMIN: DASHBOARD FEE OVERVIEW CHART
# ==========================================
class FeeOverviewChartView(APIView):
    """
    GET: Admin dashboard chart-ready fee overview.
    Optional params:
      - academic_year: e.g. 2026-2027
      - months: trend window (default 6, min 1, max 24)
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        try:
            academic_year = request.query_params.get('academic_year')
            months_param = request.query_params.get('months', '6')

            try:
                months = int(months_param)
                if months < 1 or months > 24:
                    return Response(
                        {"error": "months must be between 1 and 24"},
                        status=400
                    )
            except (TypeError, ValueError):
                return Response({"error": "months must be a valid integer"}, status=400)

            def _to_float(value):
                return round(float(value or 0), 2)

            def _percentage(part, whole):
                whole_val = float(whole or 0)
                if whole_val <= 0:
                    return 0.0
                return round((float(part or 0) / whole_val) * 100, 2)

            due_expression = ExpressionWrapper(
                F('total_amount') - F('paid_amount') - F('concession_amount'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )

            ledgers = StudentFee.objects.filter(
                fee_definition__is_active=True
            ).select_related(
                'student__section__standard',
                'fee_definition'
            )
            payments = FeePayment.objects.filter(
                payment_status='SUCCESS',
                student_fee__fee_definition__is_active=True
            ).select_related('student_fee__fee_definition')

            if academic_year:
                ledgers = ledgers.filter(fee_definition__academic_year=academic_year)
                payments = payments.filter(student_fee__fee_definition__academic_year=academic_year)

            summary_aggs = ledgers.aggregate(
                total_assigned=Sum('total_amount'),
                total_collected=Sum('paid_amount'),
                total_concession=Sum('concession_amount'),
                total_pending=Sum(due_expression),
                total_records=Count('id')
            )

            total_assigned = summary_aggs['total_assigned'] or Decimal('0.00')
            total_collected = summary_aggs['total_collected'] or Decimal('0.00')
            total_concession = summary_aggs['total_concession'] or Decimal('0.00')
            total_pending = summary_aggs['total_pending'] or Decimal('0.00')
            total_records = summary_aggs['total_records'] or 0
            effective_collectable = total_assigned - total_concession

            # Build month buckets (oldest -> newest)
            today = timezone.now().date()
            current_month_start = today.replace(day=1)
            month_starts = []
            for i in range(months - 1, -1, -1):
                year = current_month_start.year
                month = current_month_start.month - i
                while month <= 0:
                    month += 12
                    year -= 1
                month_starts.append(datetime(year, month, 1).date())

            first_month_start = month_starts[0]
            monthly_raw = payments.filter(
                payment_date__gte=first_month_start
            ).values(
                'payment_date__year', 'payment_date__month'
            ).annotate(
                collected=Sum('amount_paid'),
                transactions=Count('id')
            )

            monthly_map = {
                (row['payment_date__year'], row['payment_date__month']): row
                for row in monthly_raw
            }
            monthly_trend = []
            for m_start in month_starts:
                row = monthly_map.get((m_start.year, m_start.month), {})
                monthly_trend.append({
                    "month": m_start.strftime("%b %Y"),
                    "month_key": m_start.strftime("%Y-%m"),
                    "collected": _to_float(row.get('collected', 0)),
                    "transactions": row.get('transactions', 0) or 0
                })

            # Fee-type chart
            fee_type_rows = ledgers.values('fee_definition__fee_type').annotate(
                assigned=Sum('total_amount'),
                collected=Sum('paid_amount'),
                concession=Sum('concession_amount'),
                pending=Sum(due_expression),
                students=Count('student', distinct=True)
            ).order_by('-pending', 'fee_definition__fee_type')

            fee_type_breakdown = []
            for row in fee_type_rows:
                assigned = row['assigned'] or 0
                concession = row['concession'] or 0
                collectable = assigned - concession
                collected = row['collected'] or 0
                fee_type_breakdown.append({
                    "fee_type": row['fee_definition__fee_type'],
                    "assigned": _to_float(assigned),
                    "collected": _to_float(collected),
                    "pending": _to_float(row['pending'] or 0),
                    "concession": _to_float(concession),
                    "students": row['students'] or 0,
                    "collection_rate": _percentage(collected, collectable)
                })

            # Status distribution chart
            status_rows = ledgers.values('status').annotate(count=Count('id')).order_by('status')
            status_distribution = []
            for row in status_rows:
                count = row['count'] or 0
                status_distribution.append({
                    "status": row['status'],
                    "count": count,
                    "percentage": _percentage(count, total_records)
                })

            # Payment mode donut chart
            mode_rows = payments.values('payment_mode').annotate(
                amount=Sum('amount_paid'),
                count=Count('id')
            ).order_by('-amount')

            payment_mode_breakdown = []
            for row in mode_rows:
                payment_mode_breakdown.append({
                    "mode": row['payment_mode'],
                    "amount": _to_float(row['amount'] or 0),
                    "count": row['count'] or 0,
                    "percentage": _percentage(row['amount'] or 0, total_collected)
                })

            # Top pending classes (bar chart)
            class_rows = ledgers.exclude(status='PAID').values(
                'student__section__standard__name'
            ).annotate(
                pending=Sum(due_expression),
                students=Count('student', distinct=True)
            ).order_by('-pending')[:8]

            class_pending_top = []
            for row in class_rows:
                class_pending_top.append({
                    "class": row['student__section__standard__name'] or "Unassigned",
                    "pending": _to_float(row['pending'] or 0),
                    "students_with_due": row['students'] or 0
                })

            last_payment_date = payments.aggregate(last=Max('payment_date'))['last']
            has_data = total_records > 0 or payments.exists()

            empty_message = ""
            if not has_data:
                empty_message = "No fee data found for the selected filters."

            return Response({
                "status": 200,
                "message": "Fee overview generated successfully" if has_data else empty_message,
                "filters": {
                    "academic_year": academic_year or "ALL",
                    "months": months
                },
                "summary_cards": {
                    "total_assigned": _to_float(total_assigned),
                    "total_collected": _to_float(total_collected),
                    "total_pending": _to_float(total_pending),
                    "total_concession": _to_float(total_concession),
                    "collection_rate": _percentage(total_collected, effective_collectable)
                },
                "charts": {
                    "monthly_collection": monthly_trend,
                    "fee_type_breakdown": fee_type_breakdown,
                    "status_distribution": status_distribution,
                    "payment_mode_breakdown": payment_mode_breakdown,
                    "top_pending_classes": class_pending_top
                },
                "meta": {
                    "has_data": has_data,
                    "total_fee_records": total_records,
                    "last_payment_date": last_payment_date
                }
            }, status=200)
        except Exception as e:
            logger.exception("Fee overview chart API failed")
            return Response({
                "error": "Failed to generate fee overview chart",
                "details": str(e)
            }, status=500)


# ==========================================
# 13. ADMIN: DELETE FEE STRUCTURE (Soft Delete)
# ==========================================
class DeleteFeeView(APIView):
    """
    DELETE: Soft delete a Fee Structure (mark as inactive).
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def delete(self, request):
        fee_id = request.data.get('fee_id')

        if not fee_id:
            return Response({"error": "fee_id is required"}, status=400)

        try:
            fee_def = FeeDefinition.objects.get(id=fee_id)
        except FeeDefinition.DoesNotExist:
            return Response({"error": "Fee Definition not found"}, status=404)

        # Check if any payments exist
        has_payments = FeePayment.objects.filter(
            student_fee__fee_definition=fee_def,
            payment_status='SUCCESS'
        ).exists()

        if has_payments:
            paid_students_count = StudentFee.objects.filter(
                fee_definition=fee_def,
                payments__payment_status='SUCCESS'
            ).distinct().count()
            return Response({
                "error": "Cannot delete fee structure because one or more students have already paid.",
                "fee_type": fee_def.fee_type,
                "academic_year": fee_def.academic_year,
                "class_name": fee_def.standard.name,
                "paid_students_count": paid_students_count,
            }, status=400)
        else:
            # No payments, can hard delete
            fee_name = f"{fee_def.fee_type} ({fee_def.academic_year}) for {fee_def.standard.name}"
            _log_fee_audit(
                action='DELETE',
                request=request,
                details={
                    "operation": "delete_fee_structure",
                    "fee_id": fee_id,
                    "mode": "hard_delete",
                    "fee_type": fee_def.fee_type,
                    "academic_year": fee_def.academic_year,
                    "class_name": fee_def.standard.name,
                },
                severity='WARNING',
                response_status=200,
            )
            fee_def.delete()
            return Response({
                "status": 200,
                "message": f"Successfully deleted: {fee_name}"
            })


# ==========================================
# 14. FEE TYPES LIST
# ==========================================
class FeeTypeListView(APIView):
    """
    GET: Get a list of unique Fee Types only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unique_types = FeeDefinition.objects.filter(
            is_active=True
        ).values_list('fee_type', flat=True).distinct().order_by('fee_type')
        
        return Response({
            "status": 200,
            "count": len(set(list(unique_types)) | {HOSTEL_FEE_TYPE}),
            "fee_types": sorted(set(list(unique_types)) | {HOSTEL_FEE_TYPE})
        })


# ==========================================
# 15. ADMIN: APPLY CONCESSION
# ==========================================
class ApplyConcessionView(APIView):
    """
    Manage Student Concessions (Scholarships/Discounts).
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        """View active concessions"""
        s_id = request.query_params.get('student_id')
        year = request.query_params.get('academic_year')

        if not year:
            return Response({"error": "academic_year is required"}, 400)

        concessions = FeeConcession.objects.filter(
            is_active=True,
            fee_definition__academic_year=year
        ).select_related('student', 'fee_definition', 'fee_definition__standard', 'student__section__standard')

        if s_id:
            concessions = concessions.filter(student__student_id=s_id)

        data = []
        for c in concessions:
            ledger = StudentFee.objects.filter(
                student=c.student,
                fee_definition=c.fee_definition
            ).first()
            balance_due = ledger.due_amount if ledger else Decimal('0.00')
            paid_amount = ledger.paid_amount if ledger else Decimal('0.00')
            data.append({
                "id": c.id,
                "student_id": c.student.student_id,
                "student_name": c.student.student_name,
                "class": f"{c.student.section.standard.name}-{c.student.section.name}",
                "academic_year": c.fee_definition.academic_year,
                "fee_type": c.fee_definition.fee_type,
                "total_fee": c.fee_definition.amount,
                "concession_amount": c.amount,
                "effective_fee": max(Decimal('0.00'), c.fee_definition.amount - c.amount),
                "paid_amount": paid_amount,
                "balance_due": balance_due,
                "concession_type": c.concession_type,
            })

        return Response({
            "academic_year": year,
            "count": len(data),
            "concessions": data
        })

    def post(self, request):
        """Apply new concessions"""
        s_id = request.data.get('student_id')
        year = request.data.get('academic_year')
        discounts_list = request.data.get('discounts')

        if not s_id or not discounts_list or not isinstance(discounts_list, list):
            return Response({
                "error": "Invalid format. Provide 'student_id', 'academic_year' and 'discounts' list."
            }, 400)

        if year and not re.match(r'^\d{4}-\d{4}$', year):
            return Response({"error": "Invalid Academic Year. Use 'YYYY-YYYY'."}, 400)

        try:
            student = Student.objects.get(student_id=s_id)
        except Student.DoesNotExist:
            return Response({"error": "Student not found"}, 404)

        # Fee structure must exist before concession can be applied.
        assigned_ledgers = StudentFee.objects.filter(
            student=student,
            fee_definition__is_active=True
        )
        if year:
            assigned_ledgers = assigned_ledgers.filter(fee_definition__academic_year=year)
        if not assigned_ledgers.exists():
            if year:
                return Response({
                    "error": "Cannot apply concession because fee structure is not set for this student in the selected academic year.",
                    "student_id": s_id,
                    "academic_year": year,
                }, 400)
            return Response({
                "error": "Cannot apply concession because fee structure is not set for this student.",
                "student_id": s_id,
            }, 400)

        results = []
        
        with transaction.atomic():
            for item in discounts_list:
                f_type = item.get('fee_type')
                raw_amount = item.get('discount_amount')

                if not f_type or raw_amount is None:
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Missing type or amount"
                    })
                    continue
                
                try:
                    discount_decimal = Decimal(str(raw_amount))
                    if discount_decimal < 0:
                        results.append({
                            "fee_type": f_type,
                            "status": "Failed",
                            "reason": "Discount cannot be negative"
                        })
                        continue
                except:
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Invalid amount format"
                    })
                    continue

                # Find the Ledger Entry
                ledgers = StudentFee.objects.filter(
                    student=student,
                    fee_definition__fee_type__iexact=f_type,
                    fee_definition__is_active=True
                )
                
                if year:
                    ledgers = ledgers.filter(fee_definition__academic_year=year)

                if not ledgers.exists():
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Fee structure is not set for this fee type"
                    })
                    continue
                
                ledger = ledgers.first()

                # Prevent concession if this fee ledger is already fully settled.
                if ledger.due_amount <= Decimal('0.00') or ledger.paid_amount >= ledger.effective_total:
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Concession cannot be applied because this fee is already fully paid"
                    })
                    continue

                existing_concession = FeeConcession.objects.filter(
                    student=student,
                    fee_definition=ledger.fee_definition,
                    is_active=True
                ).order_by('-created_at').first()
                if existing_concession:
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Concession already assigned for this fee type. Do you want to update it?",
                        "concession_id": existing_concession.id,
                        "attempted_amount": str(discount_decimal),
                    })
                    continue
                
                if discount_decimal > ledger.total_amount:
                    results.append({
                        "fee_type": f_type,
                        "status": "Failed",
                        "reason": "Discount exceeds total fee amount"
                    })
                    continue

                # Create concession record
                FeeConcession.objects.create(
                    student=student,
                    fee_definition=ledger.fee_definition,
                    concession_type=item.get('concession_type', 'OTHER'),
                    amount=discount_decimal,
                    percentage=item.get('percentage'),
                    approved_by=request.user,
                    valid_from=item.get('valid_from', timezone.now().date()),
                    valid_to=item.get('valid_to', timezone.now().date() + timedelta(days=365)),
                    reason=item.get('reason', '')
                )

                # Update ledger
                ledger.concession_amount = discount_decimal
                ledger.update_status()
                ledger.save()

                results.append({
                    "fee_type": f_type,
                    "status": "Success",
                    "new_due": ledger.due_amount
                })

        successful = sum(1 for item in results if item.get("status") == "Success")
        failed = len(results) - successful
        if successful == 0:
            return Response({
                "status": 400,
                "error": "Concession not applied. All requested items failed validation.",
                "student": student.student_name,
                "report": results
            }, 400)
        _log_fee_audit(
            action='UPDATE',
            request=request,
            details={
                "operation": "apply_concession",
                "student_id": s_id,
                "academic_year": year,
                "total_items": len(results),
                "success_count": successful,
                "failed_count": failed,
            },
            response_status=200,
        )

        return Response({
            "status": 200,
            "student": student.student_name,
            "report": results
        })


    def put(self, request):
        """Edit existing concession"""
        concession_id = request.data.get('concession_id')
        new_amount = request.data.get('new_amount')

        if not concession_id or new_amount is None:
            return Response({"error": "concession_id and new_amount required"}, 400)

        try:
            concession = FeeConcession.objects.get(id=concession_id, is_active=True)
            decimal_amount = Decimal(str(new_amount))
            
            if decimal_amount < 0:
                return Response({"error": "Amount cannot be negative"}, 400)

            ledger = StudentFee.objects.get(
                student=concession.student,
                fee_definition=concession.fee_definition
            )
            if ledger.due_amount <= Decimal('0.00') or ledger.paid_amount >= ledger.effective_total:
                return Response({"error": "Concession cannot be updated because this fee is already fully paid"}, 400)
            if decimal_amount > ledger.total_amount:
                return Response({"error": "Concession cannot exceed total fee amount"}, 400)

            # Update concession
            concession.amount = decimal_amount
            concession.save()

            # Update corresponding ledger
            ledger.concession_amount = decimal_amount
            ledger.update_status()
            ledger.save()

            _log_fee_audit(
                action='UPDATE',
                request=request,
                details={
                    "operation": "update_concession",
                    "concession_id": concession_id,
                    "student_id": ledger.student.student_id,
                    "fee_type": concession.fee_definition.fee_type,
                    "new_concession": str(decimal_amount),
                    "new_due": str(ledger.due_amount),
                },
                response_status=200,
            )
            return Response({
                "message": "Concession updated successfully",
                "student": ledger.student.student_name,
                "fee_type": concession.fee_definition.fee_type,
                "new_concession": decimal_amount,
                "new_due": ledger.due_amount
            })

        except FeeConcession.DoesNotExist:
            return Response({"error": "Concession not found"}, 404)
        except StudentFee.DoesNotExist:
            return Response({"error": "Associated fee record not found"}, 404)

    def delete(self, request):
        """Remove concession"""
        concession_id = request.query_params.get('concession_id')

        if not concession_id:
            return Response({"error": "concession_id required"}, 400)

        try:
            concession = FeeConcession.objects.get(id=concession_id, is_active=True)
            
            # Soft delete
            concession.is_active = False
            concession.save()

            # Update ledger
            ledger = StudentFee.objects.get(
                student=concession.student,
                fee_definition=concession.fee_definition
            )
            ledger.concession_amount = Decimal('0.00')
            ledger.update_status()
            ledger.save()

            _log_fee_audit(
                action='DELETE',
                request=request,
                details={
                    "operation": "remove_concession",
                    "concession_id": concession_id,
                    "student_id": concession.student.student_id,
                    "fee_type": concession.fee_definition.fee_type,
                    "current_due": str(ledger.due_amount),
                },
                severity='WARNING',
                response_status=200,
            )
            return Response({
                "status": "success",
                "message": f"Concession removed for {concession.fee_definition.fee_type}",
                "current_due": ledger.due_amount
            })

        except FeeConcession.DoesNotExist:
            return Response({"error": "Concession not found"}, 404)


# ==========================================
# 16. RAZORPAY WEBHOOK HANDLER
# ==========================================


class RazorpayWebhookView(APIView):
    """
    Handle Razorpay webhook events
    """
    permission_classes = []  # No authentication for webhook
    authentication_classes = []  # No authentication needed

    def post(self, request):
        import hashlib
        import hmac
        
        logger.info("Razorpay webhook received at %s", timezone.now())
        
        webhook_signature = request.headers.get('X-Razorpay-Signature')
        
        if not webhook_signature:
            logger.error("Missing webhook signature")
            _log_fee_audit(
                action='PAYMENT_FAILED',
                details={
                    "operation": "razorpay_webhook",
                    "reason": "missing_signature",
                },
                severity='SECURITY',
                response_status=400,
            )
            return Response({"error": "Missing signature"}, status=400)

        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not secret:
            logger.error("Webhook secret not configured")
            return Response({"error": "Webhook secret not configured"}, status=500)

        body = request.body
        logger.debug("Webhook payload length: %s bytes", len(body))

        expected = hmac.new(
            key=secret.encode('utf-8'),
            msg=body,
            digestmod=hashlib.sha256
        ).hexdigest()
        logger.debug("Webhook signature match: %s", hmac.compare_digest(expected, webhook_signature))

        # Verify webhook signature
        is_valid = razorpay.verify_webhook(
            request.body,
            webhook_signature
        )

        if not is_valid:
            logger.warning("Invalid webhook signature")
            _log_fee_audit(
                action='PAYMENT_FAILED',
                details={
                    "operation": "razorpay_webhook",
                    "reason": "invalid_signature",
                },
                severity='SECURITY',
                response_status=400,
            )
            return Response({"error": "Invalid signature"}, status=400)

        logger.info("Webhook signature verified")

        payload = request.data
        event = payload.get('event')
        logger.info("Webhook event: %s", event)
        
        # Handle the webhook
        try:
            if event == 'payment.captured':
                self.handle_payment_captured(payload)
            elif event == 'payment.failed':
                self.handle_payment_failed(payload)
            elif event == 'order.paid':
                self.handle_order_paid(payload)
            elif event == 'payment.refunded':
                self.handle_payment_refunded(payload)
            elif event == 'refund.processed':
                self.handle_refund_processed(payload)
            else:
                logger.info("Unhandled webhook event: %s", event)
            
            return Response({"status": "received"})
            
        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            logger.exception(e)
            _log_fee_audit(
                action='SYSTEM_ERROR',
                details={
                    "operation": "razorpay_webhook",
                    "event": event,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )
            return Response({"error": str(e)}, status=500)

    def handle_payment_captured(self, payload):
        """Handle successful payment"""
        logger.info("Handling payment.captured webhook")
        
        try:
            payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
            payment_id = payment_entity.get('id')
            order_id = payment_entity.get('order_id')
            amount_paise = payment_entity.get('amount')

            logger.info(f"Payment captured - Order: {order_id}, Payment: {payment_id}, Amount: {amount_paise}")

            if not payment_id or not order_id:
                logger.warning("Webhook payment.captured missing ids")
                return

            with transaction.atomic():
                # Find the payment record
                payment = FeePayment.objects.select_for_update().select_related(
                    'student_fee', 'student_fee__fee_definition'
                ).get(razorpay_order_id=order_id)
                
                # Check if already processed
                if payment.payment_status == 'SUCCESS':
                    logger.info(f"Payment {payment_id} already processed")
                    return
                
                # Verify amount
                if amount_paise and int(amount_paise) != int(payment.amount_paid * 100):
                    logger.warning(f"Amount mismatch for order {order_id}")
                    payment.payment_status = 'FAILED'
                    payment.remarks = "Webhook validation failed: amount mismatch"
                    payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
                    return

                # Update payment record
                payment.razorpay_payment_id = payment_id
                payment.payment_status = 'SUCCESS'
                payment.payment_date = timezone.now().date()
                payment.save()
                
                # Update student fee ledger
                ledger = payment.student_fee
                ledger.paid_amount = ledger.payments.filter(
                    payment_status='SUCCESS'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
                ledger.last_payment_date = payment.payment_date
                ledger.update_status()
                ledger.save()
                
                logger.info("Payment %s processed successfully via webhook", payment_id)
                _log_fee_audit(
                    action='PAYMENT_PROCESSED',
                    details={
                        "operation": "fee_payment_confirmed",
                        "source": "razorpay_webhook",
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "transaction_id": payment.transaction_id,
                        "amount": str(payment.amount_paid),
                        "payment_mode": payment.payment_mode,
                        "academic_year": payment.student_fee.fee_definition.academic_year,
                        "status": "processed",
                    },
                    response_status=200,
                )
                
        except FeePayment.DoesNotExist:
            logger.error(f"Order not found for webhook: {order_id}")
            _log_fee_audit(
                action='PAYMENT_FAILED',
                details={
                    "operation": "webhook_payment_captured",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "reason": "order_not_found",
                },
                severity='WARNING',
                response_status=404,
            )
        except Exception as e:
            logger.error(f"Error processing payment.captured: {str(e)}")
            logger.exception(e)
            _log_fee_audit(
                action='SYSTEM_ERROR',
                details={
                    "operation": "webhook_payment_captured",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )

    def handle_order_paid(self, payload):
        """Handle order.paid webhook by reconciling linked payment record."""
        logger.info("Handling order.paid webhook")

        try:
            order_entity = payload.get('payload', {}).get('order', {}).get('entity', {})
            order_id = order_entity.get('id')

            # Razorpay may include payments list on order entity.
            payment_candidates = order_entity.get('payments') or []
            payment_id = None
            if isinstance(payment_candidates, list) and payment_candidates:
                first_payment = payment_candidates[0] or {}
                payment_id = first_payment.get('id')

            if not order_id:
                logger.warning("Webhook order.paid missing order_id")
                return

            with transaction.atomic():
                payment = FeePayment.objects.select_for_update().select_related(
                    'student_fee', 'student_fee__fee_definition'
                ).get(razorpay_order_id=order_id)

                if payment.payment_status == 'SUCCESS':
                    logger.info("Order %s already processed", order_id)
                    return

                payment.payment_status = 'SUCCESS'
                if payment_id:
                    payment.razorpay_payment_id = payment_id
                payment.payment_date = timezone.now().date()
                payment.save()

                ledger = payment.student_fee
                ledger.paid_amount = ledger.payments.filter(
                    payment_status='SUCCESS'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
                ledger.last_payment_date = payment.payment_date
                ledger.update_status()
                ledger.save()

                _log_fee_audit(
                    action='PAYMENT_PROCESSED',
                    details={
                        "operation": "fee_payment_confirmed",
                        "source": "razorpay_webhook_order_paid",
                        "order_id": order_id,
                        "payment_id": payment.razorpay_payment_id,
                        "transaction_id": payment.transaction_id,
                        "amount": str(payment.amount_paid),
                        "payment_mode": payment.payment_mode,
                        "academic_year": payment.student_fee.fee_definition.academic_year,
                        "status": "processed",
                    },
                    response_status=200,
                )
        except FeePayment.DoesNotExist:
            logger.error("Order not found for order.paid webhook: %s", order_id)
        except Exception as e:
            logger.error("Error processing order.paid: %s", str(e))
            logger.exception(e)

    def _process_refund_event(self, payment_id, refund_id, refund_amount, source_operation):
        """Shared refund processor for payment.refunded/refund.processed."""
        if not payment_id:
            logger.warning("%s missing payment_id", source_operation)
            return

        with transaction.atomic():
            payment = FeePayment.objects.select_for_update().get(razorpay_payment_id=payment_id)
            if not refund_id:
                logger.warning("Refund event missing refund_id for payment %s", payment_id)
                return

            if FeeRefundEvent.objects.filter(refund_id=refund_id).exists():
                logger.info("Refund %s already processed", refund_id)
                return

            refund_amount_rupees = Decimal('0.00')
            if refund_amount is not None:
                refund_amount_rupees = (Decimal(str(refund_amount)) / Decimal('100')).quantize(Decimal('0.01'))

            remaining_refundable = max(Decimal('0.00'), payment.amount_paid - payment.refunded_amount)
            applied_refund = min(refund_amount_rupees, remaining_refundable)

            FeeRefundEvent.objects.create(
                payment=payment,
                refund_id=refund_id,
                amount=applied_refund
            )

            payment.refunded_amount = (payment.refunded_amount + applied_refund).quantize(Decimal('0.01'))
            if payment.refunded_amount >= payment.amount_paid:
                payment.payment_status = 'REFUNDED'
            else:
                payment.payment_status = 'REFUND_PENDING'
            payment.remarks = f"Refunded: {refund_id}, amount: {applied_refund}"
            payment.save(update_fields=['refunded_amount', 'payment_status', 'remarks', 'updated_at'])

            ledger = payment.student_fee
            ledger.paid_amount = ledger.payments.filter(
                payment_status='SUCCESS'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
            ledger.update_status()
            ledger.save()

            _log_fee_audit(
                action='UPDATE',
                details={
                    "operation": source_operation,
                    "payment_id": payment_id,
                    "refund_id": refund_id,
                    "refund_amount": str(applied_refund),
                },
                response_status=200,
            )

    def handle_payment_failed(self, payload):
        """Handle failed payment"""
        logger.info("Handling payment.failed webhook")
        
        try:
            payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
            payment_id = payment_entity.get('id')
            order_id = payment_entity.get('order_id')
            error_code = payment_entity.get('error_code')
            error_description = payment_entity.get('error_description')

            logger.warning(f"Payment failed - Order: {order_id}, Error: {error_code} - {error_description}")

            if not order_id:
                logger.warning("Webhook payment.failed missing order_id")
                return

            try:
                payment = FeePayment.objects.get(razorpay_order_id=order_id)
                
                if payment.payment_status == 'SUCCESS':
                    logger.info(f"Payment {payment_id} already marked as success, ignoring failure")
                    return
                    
                payment.payment_status = 'FAILED'
                payment.remarks = f"Payment failed: {error_code} - {error_description}"
                payment.save()
                
                logger.info("Payment failure recorded: %s", payment_id)
                _log_fee_audit(
                    action='PAYMENT_FAILED',
                    details={
                        "operation": "webhook_payment_failed",
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "error_code": error_code,
                        "error_description": error_description,
                    },
                    severity='ERROR',
                    response_status=200,
                )
                
            except FeePayment.DoesNotExist:
                logger.error(f"Order not found for failed payment: {order_id}")
                _log_fee_audit(
                    action='PAYMENT_FAILED',
                    details={
                        "operation": "webhook_payment_failed",
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "reason": "order_not_found",
                    },
                    severity='WARNING',
                    response_status=404,
                )
                
        except Exception as e:
            logger.error(f"Error processing payment.failed: {str(e)}")
            logger.exception(e)
            _log_fee_audit(
                action='SYSTEM_ERROR',
                details={
                    "operation": "webhook_payment_failed",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )

    def handle_payment_refunded(self, payload):
        """Handle refund"""
        logger.info("Handling payment.refunded webhook")
        
        try:
            payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
            refund_entity = payload.get('payload', {}).get('refund', {}).get('entity', {})
            payment_id = payment_entity.get('id')
            refund_id = refund_entity.get('id')
            refund_amount = refund_entity.get('amount')

            logger.info(f"Payment refunded - Payment: {payment_id}, Refund: {refund_id}, Amount: {refund_amount}")
            self._process_refund_event(
                payment_id=payment_id,
                refund_id=refund_id,
                refund_amount=refund_amount,
                source_operation="webhook_payment_refunded",
            )
                
        except FeePayment.DoesNotExist:
            logger.error(f"Payment not found for refund: {payment_id}")
            _log_fee_audit(
                action='PAYMENT_FAILED',
                details={
                    "operation": "webhook_payment_refunded",
                    "payment_id": payment_id,
                    "refund_id": refund_id,
                    "reason": "payment_not_found",
                },
                severity='WARNING',
                response_status=404,
            )
        except Exception as e:
            logger.error(f"Error processing payment.refunded: {str(e)}")
            logger.exception(e)
            _log_fee_audit(
                action='SYSTEM_ERROR',
                details={
                    "operation": "webhook_payment_refunded",
                    "payment_id": payment_id,
                    "refund_id": refund_id,
                    "error": str(e),
                },
                severity='ERROR',
                response_status=500,
            )

    def handle_refund_processed(self, payload):
        """Handle refund.processed webhook."""
        logger.info("Handling refund.processed webhook")

        try:
            refund_entity = payload.get('payload', {}).get('refund', {}).get('entity', {})
            payment_id = refund_entity.get('payment_id')
            refund_id = refund_entity.get('id')
            refund_amount = refund_entity.get('amount')

            logger.info(
                "Refund processed - Payment: %s, Refund: %s, Amount: %s",
                payment_id, refund_id, refund_amount
            )

            self._process_refund_event(
                payment_id=payment_id,
                refund_id=refund_id,
                refund_amount=refund_amount,
                source_operation="webhook_refund_processed",
            )

        except FeePayment.DoesNotExist:
            logger.error("Payment not found for refund.processed: %s", payment_id)
        except Exception as e:
            logger.error("Error processing refund.processed: %s", str(e))
            logger.exception(e)
