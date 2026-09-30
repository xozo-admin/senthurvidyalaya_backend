# salary/views.py (Complete updated file)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from calendar import monthrange, month_name
from datetime import date, timedelta, datetime
from decimal import Decimal
from django.db.models import Q, Sum, Count, TextField
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.conf import settings
import uuid
import hashlib
import hmac
import time
import random

from .models import (
    SalaryStructure, TeacherSalaryStructure, 
    StaffSalaryPayment, TeacherSalaryPayment, PaymentBatch
)
from attendance.models import StaffAttendance, TeacherAttendance
from leave_management.models import LeaveRequest
from holidays.models import Holiday 
from staff.permissions import IsAdmin
from staff.permissions import IsOwnerAdminOrFinanceAdminStaff
from staff.models import NonTeachingStaff
from teachers.models import Teacher
from .serializers import (
    SalaryStructureSerializer, TeacherSalaryStructureSerializer,
    StaffSalaryPaymentSerializer, TeacherSalaryPaymentSerializer
)
from .banking import BankTransferService, BulkPaymentProcessor
from .security import EnhancedIsAdmin, PaymentSecurityMixin, AuditLogger, IsOwnerOrAdmin, PaymentTransactionPermission
from audit.models import AuditLog
from audit.serializers import AuditLogSerializer

RAZORPAYX_BANK_CODE = 'RAZORPAYX'


def _parse_int(raw_value, field_name, default=None):
    if raw_value is None:
        return default, None
    try:
        return int(raw_value), None
    except (TypeError, ValueError):
        return None, f"{field_name} must be an integer"


def _parse_int_param(raw_value, default, min_value=None, max_value=None):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default, False

    if min_value is not None and value < min_value:
        return default, False
    if max_value is not None and value > max_value:
        return default, False
    return value, True


def _validate_month_year(month, year):
    if month is None or year is None:
        return "month and year are required"
    if not 1 <= month <= 12:
        return "month must be between 1 and 12"
    if not 2000 <= year <= 2100:
        return "year must be between 2000 and 2100"
    return None


def _validate_not_future_period(month, year):
    today = date.today()
    if (year, month) > (today.year, today.month):
        return "cannot process salary for a future month"
    return None


def _get_limit_day(year: int, month: int) -> int:
    _, days_in_month = monthrange(year, month)
    return days_in_month


def _calculate_working_days(year: int, month: int, limit_day: int, holiday_dates) -> int:
    holiday_set = set(holiday_dates)
    working_days = 0
    for day in range(1, limit_day + 1):
        current_date = date(year, month, day)
        # Exclude Sundays and holidays from payable day split.
        if current_date.weekday() == 6 or current_date in holiday_set:
            continue
        working_days += 1
    return working_days


def _next_month_year(month: int, year: int):
    if month == 12:
        return 1, year + 1
    return month + 1, year


def _log_salary_failure(request, failure_type, message, details=None, response_status=400, severity='ERROR'):
    payload = {
        "module": "salary",
        "operation": "salary_payment_failure",
        "failure_type": failure_type,
        "failure_source": "bank" if "bank" in str(failure_type).lower() else "system",
        "message": str(message),
    }
    if isinstance(details, dict):
        payload.update(details)
    AuditLogger.log_action(
        request.user if request else None,
        'PAYMENT_FAILED',
        payload,
        request.META.get('REMOTE_ADDR') if request else None,
        request=request,
        response_status=response_status,
        severity=severity,
    )


def _salary_transfer_otp_verified_key(user_id: int) -> str:
    return f"salary_transfer_otp_verified_{user_id}"


def _require_salary_structure_otp(request):
    if cache.get(_salary_transfer_otp_verified_key(request.user.id)) is True:
        return None
    return Response(
        {"error": "OTP verification required for salary structure update/delete"},
        status=403
    )


def _resolve_razorpayx_bank_code(raw_bank_code=None):
    requested = str(raw_bank_code or RAZORPAYX_BANK_CODE).strip().upper()
    if requested and requested != RAZORPAYX_BANK_CODE:
        return None, "Only RAZORPAYX bank transfer is supported"
    return RAZORPAYX_BANK_CODE, None

# ==========================================
# 1. ADMIN: SET SALARY (STAFF)
# ==========================================
class AdminSalarySettingsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        """View staff salary structures with optional filters and pagination."""
        staff_type = (request.query_params.get('staff_type') or '').strip()
        search_text = (request.query_params.get('q') or '').strip()

        structures = SalaryStructure.objects.all().order_by('staff_type')
        if staff_type:
            structures = structures.filter(staff_type__iexact=staff_type)
        if search_text:
            structures = structures.filter(
                Q(staff_type__icontains=search_text)
                | Q(base_salary__icontains=search_text)
            )

        page_raw = request.query_params.get('page')
        page_size_raw = request.query_params.get('page_size')

        if page_raw is None and page_size_raw is None:
            serializer = SalaryStructureSerializer(structures, many=True)
            return Response({"status": 200, "data": serializer.data})

        page, valid_page = _parse_int_param(page_raw or 1, default=1, min_value=1)
        page_size, valid_page_size = _parse_int_param(page_size_raw or 10, default=10, min_value=1, max_value=100)
        if not (valid_page and valid_page_size):
            return Response({"error": "Invalid pagination. page >= 1 and page_size between 1 and 100."}, status=400)

        total = structures.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged = structures[start:end]
        serializer = SalaryStructureSerializer(paged, many=True)

        return Response({
            "status": 200,
            "data": serializer.data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1,
            },
        })

    def post(self, request):
        """ Create or Update Staff Salary Structure """
        data = request.data
        staff_type = data.get('staff_type')
        if not staff_type:
            return Response({"error": "staff_type is required"}, status=400)

        base_salary = data.get('base_salary')
        late_penalty_percentage = data.get('late_penalty_percentage', 10)
        today = date.today()

        existing = SalaryStructure.objects.filter(staff_type=staff_type).first()
        if existing:
            otp_error = _require_salary_structure_otp(request)
            if otp_error:
                return otp_error

        if not existing:
            obj = SalaryStructure.objects.create(
                staff_type=staff_type,
                base_salary=base_salary,
                late_penalty_percentage=late_penalty_percentage,
            )
            created = True
        else:
            created = False
            processed_this_month = StaffSalaryPayment.objects.filter(
                staff__role=staff_type,
                month=today.month,
                year=today.year,
                payment_status='processed',
            ).exists()

            if processed_this_month:
                effective_month, effective_year = _next_month_year(today.month, today.year)
                existing.pending_base_salary = base_salary
                existing.pending_late_penalty_percentage = late_penalty_percentage
                existing.pending_effective_month = effective_month
                existing.pending_effective_year = effective_year
                existing.pending_delete = False
                existing.save(update_fields=[
                    'pending_base_salary',
                    'pending_late_penalty_percentage',
                    'pending_effective_month',
                    'pending_effective_year',
                    'pending_delete',
                ])
                AuditLogger.log_action(
                    request.user,
                    'SALARY_STRUCTURE_UPDATED',
                    {
                        "module": "salary",
                        "entity": "staff_salary_structure",
                        "staff_type": existing.staff_type,
                        "base_salary": str(base_salary),
                        "late_penalty_percentage": late_penalty_percentage,
                        "effective_month": effective_month,
                        "effective_year": effective_year,
                        "operation": "schedule_update_next_month",
                    },
                    request.META.get('REMOTE_ADDR'),
                    request=request,
                    response_status=200,
                )
                return Response({
                    "status": 200,
                    "message": f"Current month salary already processed. Update scheduled from {effective_month}/{effective_year}."
                })

            existing.base_salary = base_salary
            existing.late_penalty_percentage = late_penalty_percentage
            existing.pending_base_salary = None
            existing.pending_late_penalty_percentage = None
            existing.pending_effective_month = None
            existing.pending_effective_year = None
            existing.pending_delete = False
            existing.save()
            obj = existing

        action = "Created" if created else "Updated"
        AuditLogger.log_action(
            request.user,
            'SALARY_STRUCTURE_CREATED' if created else 'SALARY_STRUCTURE_UPDATED',
            {
                "module": "salary",
                "entity": "staff_salary_structure",
                "staff_type": obj.staff_type,
                "base_salary": str(obj.base_salary),
                "late_penalty_percentage": obj.late_penalty_percentage,
                "operation": "create_or_update_structure",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": f"Salary Structure {action}"})

    def delete(self, request):
        """ Delete a Staff Salary Structure (Params: ?staff_type=driver) """
        staff_type = request.query_params.get('staff_type')
        if not staff_type:
            return Response({"error": "staff_type parameter is required"}, 400)
        otp_error = _require_salary_structure_otp(request)
        if otp_error:
            return otp_error
            
        obj = get_object_or_404(SalaryStructure, staff_type=staff_type)
        today = date.today()
        processed_this_month = StaffSalaryPayment.objects.filter(
            staff__role=staff_type,
            month=today.month,
            year=today.year,
            payment_status='processed',
        ).exists()

        if processed_this_month:
            effective_month, effective_year = _next_month_year(today.month, today.year)
            obj.pending_delete = True
            obj.pending_base_salary = None
            obj.pending_late_penalty_percentage = None
            obj.pending_effective_month = effective_month
            obj.pending_effective_year = effective_year
            obj.save(update_fields=[
                'pending_delete',
                'pending_base_salary',
                'pending_late_penalty_percentage',
                'pending_effective_month',
                'pending_effective_year',
            ])
            AuditLogger.log_action(
                request.user,
                'SALARY_STRUCTURE_DELETED',
                {
                    "module": "salary",
                    "entity": "staff_salary_structure",
                    "staff_type": staff_type,
                    "effective_month": effective_month,
                    "effective_year": effective_year,
                    "operation": "schedule_delete_next_month",
                },
                request.META.get('REMOTE_ADDR'),
                request=request,
                response_status=200,
            )
            return Response({
                "status": 200,
                "message": f"Current month salary already processed. Delete scheduled from {effective_month}/{effective_year}."
            })

        obj.delete()
        AuditLogger.log_action(
            request.user,
            'SALARY_STRUCTURE_DELETED',
            {
                "module": "salary",
                "entity": "staff_salary_structure",
                "staff_type": staff_type,
                "operation": "delete_structure",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": f"Salary Structure for {staff_type} deleted"})


# ==========================================
# 2. ADMIN: SET SALARY (TEACHER)
# ==========================================
class AdminTeacherSalarySettingsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        """View teacher salary structures with optional filters and pagination."""
        teacher_id = (request.query_params.get('teacher_id') or '').strip()
        search_text = (request.query_params.get('q') or '').strip()

        structures = TeacherSalaryStructure.objects.all().order_by('teacher_id')
        if teacher_id:
            structures = structures.filter(teacher_id=teacher_id)
        if search_text:
            matching_teacher_ids = list(
                Teacher.objects.filter(name__icontains=search_text).values_list('teacher_id', flat=True)
            )
            structures = structures.filter(
                Q(teacher_id__icontains=search_text)
                | Q(base_salary__icontains=search_text)
                | Q(teacher_id__in=matching_teacher_ids)
            )

        page_raw = request.query_params.get('page')
        page_size_raw = request.query_params.get('page_size')

        if page_raw is None and page_size_raw is None:
            serializer = TeacherSalaryStructureSerializer(structures, many=True)
            return Response({"status": 200, "data": serializer.data})

        page, valid_page = _parse_int_param(page_raw or 1, default=1, min_value=1)
        page_size, valid_page_size = _parse_int_param(page_size_raw or 10, default=10, min_value=1, max_value=100)
        if not (valid_page and valid_page_size):
            return Response({"error": "Invalid pagination. page >= 1 and page_size between 1 and 100."}, status=400)

        total = structures.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged = structures[start:end]
        serializer = TeacherSalaryStructureSerializer(paged, many=True)

        return Response({
            "status": 200,
            "data": serializer.data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1,
            },
        })

    def post(self, request):
        """ Create or Update Teacher Salary Structure """
        data = request.data
        teacher_id = data.get('teacher_id')
        if not teacher_id:
            return Response({"error": "teacher_id is required"}, status=400)

        base_salary = data.get('base_salary')
        late_penalty_percentage = data.get('late_penalty_percentage', 10)
        today = date.today()

        existing = TeacherSalaryStructure.objects.filter(teacher_id=teacher_id).first()
        if existing:
            otp_error = _require_salary_structure_otp(request)
            if otp_error:
                return otp_error

        if not existing:
            obj = TeacherSalaryStructure.objects.create(
                teacher_id=teacher_id,
                base_salary=base_salary,
                late_penalty_percentage=late_penalty_percentage,
            )
            created = True
        else:
            created = False
            processed_this_month = TeacherSalaryPayment.objects.filter(
                teacher__teacher_id=teacher_id,
                month=today.month,
                year=today.year,
                payment_status='processed',
            ).exists()

            if processed_this_month:
                effective_month, effective_year = _next_month_year(today.month, today.year)
                existing.pending_base_salary = base_salary
                existing.pending_late_penalty_percentage = late_penalty_percentage
                existing.pending_effective_month = effective_month
                existing.pending_effective_year = effective_year
                existing.pending_delete = False
                existing.save(update_fields=[
                    'pending_base_salary',
                    'pending_late_penalty_percentage',
                    'pending_effective_month',
                    'pending_effective_year',
                    'pending_delete',
                ])
                AuditLogger.log_action(
                    request.user,
                    'SALARY_STRUCTURE_UPDATED',
                    {
                        "module": "salary",
                        "entity": "teacher_salary_structure",
                        "teacher_id": existing.teacher_id,
                        "base_salary": str(base_salary),
                        "late_penalty_percentage": late_penalty_percentage,
                        "effective_month": effective_month,
                        "effective_year": effective_year,
                        "operation": "schedule_update_next_month",
                    },
                    request.META.get('REMOTE_ADDR'),
                    request=request,
                    response_status=200,
                )
                return Response({
                    "status": 200,
                    "message": f"Current month salary already processed. Update scheduled from {effective_month}/{effective_year}."
                })

            existing.base_salary = base_salary
            existing.late_penalty_percentage = late_penalty_percentage
            existing.pending_base_salary = None
            existing.pending_late_penalty_percentage = None
            existing.pending_effective_month = None
            existing.pending_effective_year = None
            existing.pending_delete = False
            existing.save()
            obj = existing

        action = "Created" if created else "Updated"
        AuditLogger.log_action(
            request.user,
            'SALARY_STRUCTURE_CREATED' if created else 'SALARY_STRUCTURE_UPDATED',
            {
                "module": "salary",
                "entity": "teacher_salary_structure",
                "teacher_id": obj.teacher_id,
                "base_salary": str(obj.base_salary),
                "late_penalty_percentage": obj.late_penalty_percentage,
                "operation": "create_or_update_structure",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": f"Teacher Salary {action}"})

    def delete(self, request):
        """ Delete a Teacher Salary Structure (Params: ?teacher_id=TCH001) """
        teacher_id = request.query_params.get('teacher_id')
        if not teacher_id:
            return Response({"error": "teacher_id parameter is required"}, 400)
        otp_error = _require_salary_structure_otp(request)
        if otp_error:
            return otp_error

        obj = get_object_or_404(TeacherSalaryStructure, teacher_id=teacher_id)
        today = date.today()
        processed_this_month = TeacherSalaryPayment.objects.filter(
            teacher__teacher_id=teacher_id,
            month=today.month,
            year=today.year,
            payment_status='processed',
        ).exists()

        if processed_this_month:
            effective_month, effective_year = _next_month_year(today.month, today.year)
            obj.pending_delete = True
            obj.pending_base_salary = None
            obj.pending_late_penalty_percentage = None
            obj.pending_effective_month = effective_month
            obj.pending_effective_year = effective_year
            obj.save(update_fields=[
                'pending_delete',
                'pending_base_salary',
                'pending_late_penalty_percentage',
                'pending_effective_month',
                'pending_effective_year',
            ])
            AuditLogger.log_action(
                request.user,
                'SALARY_STRUCTURE_DELETED',
                {
                    "module": "salary",
                    "entity": "teacher_salary_structure",
                    "teacher_id": teacher_id,
                    "effective_month": effective_month,
                    "effective_year": effective_year,
                    "operation": "schedule_delete_next_month",
                },
                request.META.get('REMOTE_ADDR'),
                request=request,
                response_status=200,
            )
            return Response({
                "status": 200,
                "message": f"Current month salary already processed. Delete scheduled from {effective_month}/{effective_year}."
            })

        obj.delete()
        AuditLogger.log_action(
            request.user,
            'SALARY_STRUCTURE_DELETED',
            {
                "module": "salary",
                "entity": "teacher_salary_structure",
                "teacher_id": teacher_id,
                "operation": "delete_structure",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": f"Salary Structure for {teacher_id} deleted"})


# ==========================================
# 3. STAFF: SALARY DASHBOARD (SELF)
# ==========================================
class MySalaryDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)
        
        staff = user.staff_profile
        today = date.today()
        year, err = _parse_int(request.query_params.get('year', today.year), 'year', default=today.year)
        if err:
            return Response({"error": err}, status=400)
        month, err = _parse_int(request.query_params.get('month', today.month), 'month', default=today.month)
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)

        # Check if payment already exists for this month
        existing_payment = StaffSalaryPayment.objects.filter(
            staff=staff, month=month, year=year
        ).first()

        # 1. Get Salary Structure
        try:
            structure = SalaryStructure.objects.get(staff_type=staff.role)
        except SalaryStructure.DoesNotExist:
            return Response({"error": "Salary structure not set by Admin yet."}, 400)

        # 2. Basic Math
        base_salary, late_penalty_percentage, is_deleted = structure.get_effective_values(month, year)
        if is_deleted or base_salary is None:
            return Response({"error": "Salary structure is not active for the selected month."}, 400)
        
        # 3. Fetch Data
        holiday_dates = Holiday.objects.filter(
            date__year=year, date__month=month
        ).filter(
            Q(applicable_for='everyone') | Q(applicable_for='staff')
        ).values_list('date', flat=True)
        
        attendance_records = StaffAttendance.objects.filter(
            staff=staff, date__year=year, date__month=month
        )
        attendance_map = { rec.date: rec.status for rec in attendance_records }

        limit_day = _get_limit_day(year, month)
        working_days = _calculate_working_days(year, month, limit_day, holiday_dates)
        if working_days <= 0:
            return Response({"error": "No working days available for the selected period."}, status=400)

        per_day_wage = base_salary / Decimal(working_days)

        # 4. Calculation Loop
        days_worked = 0
        days_absent = 0
        days_late = 0

        for day in range(1, limit_day + 1):
            current_date = date(year, month, day)
            
            if current_date.weekday() == 6:
                # Sundays are excluded from staff salary calculations.
                continue

            if current_date in holiday_dates:
                continue

            if current_date in attendance_map:
                status = attendance_map[current_date]
                days_worked += 1
                
                if status == 'Late':
                    days_late += 1
            else:
                days_absent += 1

        # 5. Final Calculation
        late_penalty_amt = per_day_wage * (Decimal(str(late_penalty_percentage)) / Decimal('100'))
        total_absent_deduction = days_absent * per_day_wage
        total_late_deduction = days_late * late_penalty_amt
        
        total_deduction = total_absent_deduction + total_late_deduction
        net_payable = max(base_salary - total_deduction, Decimal('0.00'))

        response_data = {
            "month": f"{year}-{month}",
            "structure": {
                "base_salary": round(base_salary, 2),
                "per_day_wage": round(per_day_wage, 2),
                "working_days": working_days
            },
            "attendance": {
                "worked": days_worked,
                "absent": days_absent,
                "late": days_late
            },
            "deductions": {
                "absent_amount": round(total_absent_deduction, 2),
                "late_amount": round(total_late_deduction, 2),
                "total_cut": round(total_deduction, 2)
            },
            "net_payable": round(net_payable, 2)
        }

        # Add payment status if exists
        if existing_payment:
            response_data["payment_status"] = {
                "status": existing_payment.payment_status,
                "transaction_id": existing_payment.transaction_id,
                "payment_date": existing_payment.payment_date,
                "bank_reference": existing_payment.bank_reference
            }

        return Response({
            "status": 200,
            "data": response_data
        })


# ==========================================
# 4. TEACHER: SALARY DASHBOARD (SELF)
# ==========================================
class MyTeacherSalaryDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not hasattr(user, 'teacher_profile'):
            return Response({"error": "Access Denied"}, 403)
        
        teacher = user.teacher_profile
        today = date.today()
        year, err = _parse_int(request.query_params.get('year', today.year), 'year', default=today.year)
        if err:
            return Response({"error": err}, status=400)
        month, err = _parse_int(request.query_params.get('month', today.month), 'month', default=today.month)
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)

        # Check if payment already exists
        existing_payment = TeacherSalaryPayment.objects.filter(
            teacher=teacher, month=month, year=year
        ).first()

        # 1. Get Salary Structure
        try:
            structure = TeacherSalaryStructure.objects.get(teacher_id=teacher.teacher_id)
        except TeacherSalaryStructure.DoesNotExist:
            return Response({"error": "Salary structure not set by Admin yet."}, 400)

        # 2. Basic Math
        base_salary, late_penalty_percentage, is_deleted = structure.get_effective_values(month, year)
        if is_deleted or base_salary is None:
            return Response({"error": "Salary structure is not active for the selected month."}, 400)
        
        # 3. Fetch Data
        holiday_dates = Holiday.objects.filter(
            date__year=year, date__month=month
        ).filter(
            Q(applicable_for='everyone') | Q(applicable_for='teachers')
        ).values_list('date', flat=True)
        
        attendance_records = TeacherAttendance.objects.filter(
            teacher=teacher, date__year=year, date__month=month
        )
        attendance_map = { rec.date: rec.status for rec in attendance_records }

        limit_day = _get_limit_day(year, month)
        working_days = _calculate_working_days(year, month, limit_day, holiday_dates)
        if working_days <= 0:
            return Response({"error": "No working days available for the selected period."}, status=400)

        per_day_wage = base_salary / Decimal(working_days)

        # 4. Calculation Loop
        days_worked = 0
        days_absent = 0
        days_late = 0

        for day in range(1, limit_day + 1):
            current_date = date(year, month, day)
            
            if current_date.weekday() == 6:
                # Sundays are excluded from teacher salary calculations.
                continue

            if current_date in holiday_dates:
                continue

            if current_date in attendance_map:
                status = attendance_map[current_date]
                days_worked += 1 
                
                if status == 'Late':
                    days_late += 1
            else:
                days_absent += 1

        # 5. Final Calculation
        late_penalty_amt = per_day_wage * (Decimal(str(late_penalty_percentage)) / Decimal('100'))
        total_absent_deduction = days_absent * per_day_wage
        total_late_deduction = days_late * late_penalty_amt
        
        total_deduction = total_absent_deduction + total_late_deduction
        net_payable = max(base_salary - total_deduction, Decimal('0.00'))

        response_data = {
            "month": f"{year}-{month}",
            "structure": {
                "base_salary": round(base_salary, 2),
                "per_day_wage": round(per_day_wage, 2),
                "working_days": working_days
            },
            "attendance": {
                "worked": days_worked,
                "absent": days_absent,
                "late": days_late
            },
            "deductions": {
                "absent_amount": round(total_absent_deduction, 2),
                "late_amount": round(total_late_deduction, 2),
                "total_cut": round(total_deduction, 2)
            },
            "net_payable": round(net_payable, 2)
        }

        # Add payment status if exists
        if existing_payment:
            response_data["payment_status"] = {
                "status": existing_payment.payment_status,
                "transaction_id": existing_payment.transaction_id,
                "payment_date": existing_payment.payment_date,
                "bank_reference": existing_payment.bank_reference
            }

        return Response({
            "status": 200,
            "data": response_data
        })


# ==========================================
# 5. ADMIN SALARY PAYMENT VIEWS (EXISTING)
# ==========================================

class AdminStaffSalaryPaymentListView(APIView):
    """Get all staff salary payments with filters"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        staff_id = request.query_params.get('staff_id')
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        status = request.query_params.get('status')

        payments = StaffSalaryPayment.objects.all()
        
        if staff_id:
            if str(staff_id).isdigit():
                payments = payments.filter(staff_id=int(staff_id))
            else:
                payments = payments.filter(staff__staff_id=staff_id)
        if month:
            payments = payments.filter(month=month)
        if year:
            payments = payments.filter(year=year)
        if status:
            payments = payments.filter(payment_status=status)

        serializer = StaffSalaryPaymentSerializer(payments, many=True)
        return Response({"status": 200, "data": serializer.data})


class AdminStaffSalaryPaymentDetailView(APIView):
    """Get, update or delete a specific staff salary payment"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request, payment_id):
        payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        serializer = StaffSalaryPaymentSerializer(payment)
        return Response({"status": 200, "data": serializer.data})

    def put(self, request, payment_id):
        payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        
        # Update payment details
        payment.transaction_id = request.data.get('transaction_id', payment.transaction_id)
        payment.bank_reference = request.data.get('bank_reference', payment.bank_reference)
        payment.payment_status = request.data.get('payment_status', payment.payment_status)
        payment.remarks = request.data.get('remarks', payment.remarks)
        
        # If status is changed to processed, set payment date
        if payment.payment_status == 'processed' and not payment.payment_date:
            payment.payment_date = timezone.now()
        
        payment.save()
        AuditLogger.log_action(
            request.user,
            'UPDATE',
            {
                "module": "salary",
                "entity": "staff_payment",
                "payment_id": payment.id,
                "staff_id": payment.staff_id,
                "staff_name": payment.staff.name if getattr(payment, 'staff', None) else None,
                "credited_to": payment.staff.name if getattr(payment, 'staff', None) else None,
                "credited_to_type": "staff",
                "month": payment.month,
                "year": payment.year,
                "payment_status": payment.payment_status,
                "operation": "update_payment",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        
        serializer = StaffSalaryPaymentSerializer(payment)
        return Response({"status": 200, "message": "Payment updated", "data": serializer.data})

    def delete(self, request, payment_id):
        payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        staff_id = payment.staff_id
        payment.delete()
        AuditLogger.log_action(
            request.user,
            'DELETE',
            {
                "module": "salary",
                "entity": "staff_payment",
                "payment_id": payment_id,
                "staff_id": staff_id,
                "operation": "delete_payment",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": "Payment record deleted"})


class AdminProcessStaffSalaryView(APIView):
    """Calculate and create salary payment for a staff member"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        staff_id = request.data.get('staff_id')
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        
        staff = get_object_or_404(NonTeachingStaff, id=staff_id)
        
        # Check if payment already exists
        existing_payment = StaffSalaryPayment.objects.filter(
            staff=staff, month=month, year=year
        ).first()
        
        if existing_payment:
            return Response({
                "error": f"Salary payment for {month}/{year} already exists with status: {existing_payment.payment_status}"
            }, status=400)
        
        # Get salary structure
        try:
            structure = SalaryStructure.objects.get(staff_type=staff.role)
        except SalaryStructure.DoesNotExist:
            return Response({"error": "Salary structure not set for this staff type"}, 400)
        
        # Calculate salary
        try:
            calculation = self._calculate_salary(staff, month, year, structure)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        
        # Create payment record
        payment = StaffSalaryPayment.objects.create(
            staff=staff,
            month=month,
            year=year,
            base_salary=calculation['base_salary'],
            per_day_wage=calculation['per_day_wage'],
            days_worked=calculation['days_worked'],
            days_absent=calculation['days_absent'],
            days_late=calculation['days_late'],
            absent_amount=calculation['absent_amount'],
            late_amount=calculation['late_amount'],
            total_deduction=calculation['total_deduction'],
            net_payable=calculation['net_payable'],
            transfer_bank_code=RAZORPAYX_BANK_CODE,
            payment_status='pending',
            created_by=request.user
        )
        AuditLogger.log_action(
            request.user,
            'STAFF_SALARY_PROCESSED',
            {
                "module": "salary",
                "entity": "staff_payment",
                "payment_id": payment.id,
                "staff_id": staff.id,
                "staff_name": staff.name,
                "month": month,
                "year": year,
                "amount": str(calculation['net_payable']),
                "operation": "process_single_staff_salary",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        
        serializer = StaffSalaryPaymentSerializer(payment)
        return Response({
            "status": 200, 
            "message": "Salary payment record created",
            "data": serializer.data
        })
    
    def _calculate_salary(self, staff, month, year, structure):
        """Reusable salary calculation logic"""
        base_salary, late_penalty_percentage, is_deleted = structure.get_effective_values(month, year)
        if is_deleted or base_salary is None:
            raise ValueError("Salary structure is not active for the selected month.")
        
        # Get holidays
        holiday_dates = Holiday.objects.filter(
            date__year=year, date__month=month
        ).filter(
            Q(applicable_for='everyone') | Q(applicable_for='staff')
        ).values_list('date', flat=True)
        
        # Get attendance
        attendance_records = StaffAttendance.objects.filter(
            staff=staff, date__year=year, date__month=month
        )
        attendance_map = {rec.date: rec.status for rec in attendance_records}
        
        limit_day = _get_limit_day(year, month)
        working_days = _calculate_working_days(year, month, limit_day, holiday_dates)
        if working_days <= 0:
            raise ValueError("No working days available for the selected period.")

        per_day_wage = base_salary / Decimal(working_days)

        # Calculate
        days_worked = 0
        days_absent = 0
        days_late = 0
        
        for day in range(1, limit_day + 1):
            current_date = date(year, month, day)
            
            if current_date.weekday() == 6:
                # Sundays are excluded from staff salary calculations.
                continue
                
            if current_date in holiday_dates:
                continue
                
            if current_date in attendance_map:
                status = attendance_map[current_date]
                days_worked += 1
                if status == 'Late':
                    days_late += 1
            else:
                days_absent += 1
        
        # Calculate amounts
        late_penalty_amt = per_day_wage * (Decimal(str(late_penalty_percentage)) / Decimal('100'))
        total_absent_deduction = days_absent * per_day_wage
        total_late_deduction = days_late * late_penalty_amt
        total_deduction = total_absent_deduction + total_late_deduction
        net_payable = max(base_salary - total_deduction, Decimal('0.00'))
        
        return {
            'base_salary': base_salary,
            'per_day_wage': per_day_wage,
            'days_worked': days_worked,
            'days_absent': days_absent,
            'days_late': days_late,
            'absent_amount': total_absent_deduction,
            'late_amount': total_late_deduction,
            'total_deduction': total_deduction,
            'net_payable': net_payable
        }


class AdminBulkProcessStaffSalaryView(APIView):
    """Calculate and create salary payments for all staff of a specific type or all"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        staff_type = request.data.get('staff_type')  # Optional: filter by staff type
        
        # Get staff members
        staff_queryset = NonTeachingStaff.objects.all()
        if staff_type:
            staff_queryset = staff_queryset.filter(role=staff_type)
        
        payments_created = []
        errors = []
        
        for staff in staff_queryset:
            # Check if payment already exists
            if StaffSalaryPayment.objects.filter(staff=staff, month=month, year=year).exists():
                errors.append(f"Payment already exists for {staff.name}")
                continue
            
            # Get salary structure
            try:
                structure = SalaryStructure.objects.get(staff_type=staff.role)
            except SalaryStructure.DoesNotExist:
                errors.append(f"No salary structure for {staff.name} ({staff.role})")
                continue
            
            # Calculate salary
            try:
                calculation = AdminProcessStaffSalaryView()._calculate_salary(staff, month, year, structure)
            except ValueError as exc:
                errors.append(f"{staff.name}: {str(exc)}")
                continue
            
            # Create payment record
            payment = StaffSalaryPayment.objects.create(
                staff=staff,
                month=month,
                year=year,
                base_salary=calculation['base_salary'],
                per_day_wage=calculation['per_day_wage'],
                days_worked=calculation['days_worked'],
                days_absent=calculation['days_absent'],
                days_late=calculation['days_late'],
                absent_amount=calculation['absent_amount'],
                late_amount=calculation['late_amount'],
                total_deduction=calculation['total_deduction'],
                net_payable=calculation['net_payable'],
                transfer_bank_code=RAZORPAYX_BANK_CODE,
                payment_status='pending',
                created_by=request.user
            )
            
            payments_created.append({
                "staff_id": staff.id,
                "staff_name": staff.name,
                "net_payable": str(calculation['net_payable'])
            })
        
        AuditLogger.log_action(
            request.user,
            'BULK_STAFF_SALARY_PROCESSED',
            {
                "module": "salary",
                "entity": "staff_payment",
                "month": month,
                "year": year,
                "staff_type": staff_type or "ALL",
                "created_count": len(payments_created),
                "error_count": len(errors),
                "operation": "bulk_process_staff_salary",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({
            "status": 200,
            "message": f"Created {len(payments_created)} payments",
            "payments_created": payments_created,
            "errors": errors
        })
        


class AdminTeacherSalaryPaymentListView(APIView):
    """Get all teacher salary payments with filters"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        teacher_id = request.query_params.get('teacher_id')
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        status = request.query_params.get('status')

        payments = TeacherSalaryPayment.objects.all()
        
        if teacher_id:
            if str(teacher_id).isdigit():
                payments = payments.filter(teacher_id=int(teacher_id))
            else:
                payments = payments.filter(teacher__teacher_id=teacher_id)
        if month:
            payments = payments.filter(month=month)
        if year:
            payments = payments.filter(year=year)
        if status:
            payments = payments.filter(payment_status=status)

        serializer = TeacherSalaryPaymentSerializer(payments, many=True)
        return Response({"status": 200, "data": serializer.data})


class AdminTeacherSalaryPaymentDetailView(APIView):
    """Get, update or delete a specific teacher salary payment"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request, payment_id):
        payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        serializer = TeacherSalaryPaymentSerializer(payment)
        return Response({"status": 200, "data": serializer.data})

    def put(self, request, payment_id):
        payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        
        payment.transaction_id = request.data.get('transaction_id', payment.transaction_id)
        payment.bank_reference = request.data.get('bank_reference', payment.bank_reference)
        payment.payment_status = request.data.get('payment_status', payment.payment_status)
        payment.remarks = request.data.get('remarks', payment.remarks)
        
        if payment.payment_status == 'processed' and not payment.payment_date:
            payment.payment_date = timezone.now()
        
        payment.save()
        AuditLogger.log_action(
            request.user,
            'UPDATE',
            {
                "module": "salary",
                "entity": "teacher_payment",
                "payment_id": payment.id,
                "teacher_id": payment.teacher_id,
                "teacher_name": payment.teacher.name if getattr(payment, 'teacher', None) else None,
                "credited_to": payment.teacher.name if getattr(payment, 'teacher', None) else None,
                "credited_to_type": "teacher",
                "month": payment.month,
                "year": payment.year,
                "payment_status": payment.payment_status,
                "operation": "update_payment",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        
        serializer = TeacherSalaryPaymentSerializer(payment)
        return Response({"status": 200, "message": "Payment updated", "data": serializer.data})

    def delete(self, request, payment_id):
        payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        teacher_id = payment.teacher_id
        payment.delete()
        AuditLogger.log_action(
            request.user,
            'DELETE',
            {
                "module": "salary",
                "entity": "teacher_payment",
                "payment_id": payment_id,
                "teacher_id": teacher_id,
                "operation": "delete_payment",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({"status": 200, "message": "Payment record deleted"})


class AdminProcessTeacherSalaryView(APIView):
    """Calculate and create salary payment for a teacher"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        teacher_id = request.data.get('teacher_id')
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        
        teacher = get_object_or_404(Teacher, id=teacher_id)
        
        # Check if payment already exists
        existing_payment = TeacherSalaryPayment.objects.filter(
            teacher=teacher, month=month, year=year
        ).first()
        
        if existing_payment:
            return Response({
                "error": f"Salary payment for {month}/{year} already exists with status: {existing_payment.payment_status}"
            }, status=400)
        
        # Get salary structure
        try:
            structure = TeacherSalaryStructure.objects.get(teacher_id=teacher.teacher_id)
        except TeacherSalaryStructure.DoesNotExist:
            return Response({"error": "Salary structure not set for this teacher"}, 400)
        
        # Calculate salary
        try:
            calculation = self._calculate_salary(teacher, month, year, structure)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        
        # Create payment record
        payment = TeacherSalaryPayment.objects.create(
            teacher=teacher,
            month=month,
            year=year,
            base_salary=calculation['base_salary'],
            per_day_wage=calculation['per_day_wage'],
            days_worked=calculation['days_worked'],
            days_absent=calculation['days_absent'],
            days_late=calculation['days_late'],
            absent_amount=calculation['absent_amount'],
            late_amount=calculation['late_amount'],
            total_deduction=calculation['total_deduction'],
            net_payable=calculation['net_payable'],
            transfer_bank_code=RAZORPAYX_BANK_CODE,
            payment_status='pending',
            created_by=request.user
        )
        AuditLogger.log_action(
            request.user,
            'TEACHER_SALARY_PROCESSED',
            {
                "module": "salary",
                "entity": "teacher_payment",
                "payment_id": payment.id,
                "teacher_id": teacher.id,
                "teacher_name": teacher.name,
                "month": month,
                "year": year,
                "amount": str(calculation['net_payable']),
                "operation": "process_single_teacher_salary",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        
        serializer = TeacherSalaryPaymentSerializer(payment)
        return Response({
            "status": 200, 
            "message": "Salary payment record created",
            "data": serializer.data
        })
    
    def _calculate_salary(self, teacher, month, year, structure):
        """Reusable teacher salary calculation logic"""
        base_salary, late_penalty_percentage, is_deleted = structure.get_effective_values(month, year)
        if is_deleted or base_salary is None:
            raise ValueError("Salary structure is not active for the selected month.")
        
        # Get holidays
        holiday_dates = Holiday.objects.filter(
            date__year=year, date__month=month
        ).filter(
            Q(applicable_for='everyone') | Q(applicable_for='teachers')
        ).values_list('date', flat=True)
        
        # Get attendance
        attendance_records = TeacherAttendance.objects.filter(
            teacher=teacher, date__year=year, date__month=month
        )
        attendance_map = {rec.date: rec.status for rec in attendance_records}
        
        limit_day = _get_limit_day(year, month)
        working_days = _calculate_working_days(year, month, limit_day, holiday_dates)
        if working_days <= 0:
            raise ValueError("No working days available for the selected period.")

        per_day_wage = base_salary / Decimal(working_days)

        # Calculate
        days_worked = 0
        days_absent = 0
        days_late = 0
        
        for day in range(1, limit_day + 1):
            current_date = date(year, month, day)
            
            if current_date.weekday() == 6:
                # Sundays are excluded from teacher salary calculations.
                continue
                
            if current_date in holiday_dates:
                continue
                
            if current_date in attendance_map:
                status = attendance_map[current_date]
                days_worked += 1
                if status == 'Late':
                    days_late += 1
            else:
                days_absent += 1
        
        # Calculate amounts
        late_penalty_amt = per_day_wage * (Decimal(str(late_penalty_percentage)) / Decimal('100'))
        total_absent_deduction = days_absent * per_day_wage
        total_late_deduction = days_late * late_penalty_amt
        total_deduction = total_absent_deduction + total_late_deduction
        net_payable = max(base_salary - total_deduction, Decimal('0.00'))
        
        return {
            'base_salary': base_salary,
            'per_day_wage': per_day_wage,
            'days_worked': days_worked,
            'days_absent': days_absent,
            'days_late': days_late,
            'absent_amount': total_absent_deduction,
            'late_amount': total_late_deduction,
            'total_deduction': total_deduction,
            'net_payable': net_payable
        }


class AdminBulkProcessTeacherSalaryView(APIView):
    """Calculate and create salary payments for all teachers"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        
        teachers = Teacher.objects.all()
        
        payments_created = []
        errors = []
        
        for teacher in teachers:
            # Check if payment already exists
            if TeacherSalaryPayment.objects.filter(teacher=teacher, month=month, year=year).exists():
                errors.append(f"Payment already exists for {teacher.name}")
                continue
            
            # Get salary structure
            try:
                structure = TeacherSalaryStructure.objects.get(teacher_id=teacher.teacher_id)
            except TeacherSalaryStructure.DoesNotExist:
                errors.append(f"No salary structure for {teacher.name} ({teacher.teacher_id})")
                continue
            
            # Calculate salary
            try:
                calculation = AdminProcessTeacherSalaryView()._calculate_salary(teacher, month, year, structure)
            except ValueError as exc:
                errors.append(f"{teacher.name}: {str(exc)}")
                continue
            
            # Create payment record
            payment = TeacherSalaryPayment.objects.create(
                teacher=teacher,
                month=month,
                year=year,
                base_salary=calculation['base_salary'],
                per_day_wage=calculation['per_day_wage'],
                days_worked=calculation['days_worked'],
                days_absent=calculation['days_absent'],
                days_late=calculation['days_late'],
                absent_amount=calculation['absent_amount'],
                late_amount=calculation['late_amount'],
                total_deduction=calculation['total_deduction'],
                net_payable=calculation['net_payable'],
                transfer_bank_code=RAZORPAYX_BANK_CODE,
                payment_status='pending',
                created_by=request.user
            )
            
            payments_created.append({
                "teacher_id": teacher.id,
                "teacher_name": teacher.name,
                "net_payable": str(calculation['net_payable'])
            })
        
        AuditLogger.log_action(
            request.user,
            'BULK_TEACHER_SALARY_PROCESSED',
            {
                "module": "salary",
                "entity": "teacher_payment",
                "month": month,
                "year": year,
                "created_count": len(payments_created),
                "error_count": len(errors),
                "operation": "bulk_process_teacher_salary",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        return Response({
            "status": 200,
            "message": f"Created {len(payments_created)} payments",
            "payments_created": payments_created,
            "errors": errors
        })


class AdminSalarySummaryView(APIView):
    """Get summary of all salary payments for a given month/year"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        current_date = date.today()
        month, err = _parse_int(request.query_params.get('month', current_date.month), 'month', default=current_date.month)
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.query_params.get('year', current_date.year), 'year', default=current_date.year)
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        
        # Staff payments summary
        staff_payments = StaffSalaryPayment.objects.filter(month=month, year=year)
        staff_total = staff_payments.aggregate(
            total=Sum('net_payable'),
            processed=Sum('net_payable', filter=Q(payment_status='processed')),
            processing=Sum('net_payable', filter=Q(payment_status='processing')),
            pending=Sum('net_payable', filter=Q(payment_status='pending')),
            failed=Sum('net_payable', filter=Q(payment_status='failed'))
        )
        
        staff_summary = {
            'total_count': staff_payments.count(),
            'processed_count': staff_payments.filter(payment_status='processed').count(),
            'processing_count': staff_payments.filter(payment_status='processing').count(),
            'pending_count': staff_payments.filter(payment_status='pending').count(),
            'failed_count': staff_payments.filter(payment_status='failed').count(),
            'total_amount': float(staff_total['total'] or 0),
            'processed_amount': float(staff_total['processed'] or 0),
            'processing_amount': float(staff_total['processing'] or 0),
            'pending_amount': float(staff_total['pending'] or 0),
            'failed_amount': float(staff_total['failed'] or 0),
        }
        
        # Teacher payments summary
        teacher_payments = TeacherSalaryPayment.objects.filter(month=month, year=year)
        teacher_total = teacher_payments.aggregate(
            total=Sum('net_payable'),
            processed=Sum('net_payable', filter=Q(payment_status='processed')),
            processing=Sum('net_payable', filter=Q(payment_status='processing')),
            pending=Sum('net_payable', filter=Q(payment_status='pending')),
            failed=Sum('net_payable', filter=Q(payment_status='failed'))
        )
        
        teacher_summary = {
            'total_count': teacher_payments.count(),
            'processed_count': teacher_payments.filter(payment_status='processed').count(),
            'processing_count': teacher_payments.filter(payment_status='processing').count(),
            'pending_count': teacher_payments.filter(payment_status='pending').count(),
            'failed_count': teacher_payments.filter(payment_status='failed').count(),
            'total_amount': float(teacher_total['total'] or 0),
            'processed_amount': float(teacher_total['processed'] or 0),
            'processing_amount': float(teacher_total['processing'] or 0),
            'pending_amount': float(teacher_total['pending'] or 0),
            'failed_amount': float(teacher_total['failed'] or 0),
        }
        
        return Response({
            "status": 200,
            "data": {
                "month": month,
                "year": year,
                "staff": staff_summary,
                "teachers": teacher_summary,
                "grand_total": float(staff_summary['total_amount'] + teacher_summary['total_amount'])
            }
        })


class AdminEmployeeSalaryYearlyReportView(APIView):
    """Month-wise salary report for a single staff/teacher for a given year."""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        employee_type = (request.query_params.get('employee_type') or '').strip().lower()
        employee_id_raw = (request.query_params.get('employee_id') or '').strip()
        year_raw = request.query_params.get('year')

        if employee_type not in {'staff', 'teacher'}:
            return Response({"error": "employee_type must be either 'staff' or 'teacher'"}, status=400)

        if not employee_id_raw:
            return Response({"error": "employee_id is required"}, status=400)

        year, year_err = _parse_int(year_raw, "year")
        if year_err:
            return Response({"error": year_err}, status=400)
        today = date.today()
        if year is None or year < 2000 or year > today.year:
            return Response({"error": f"year must be between 2000 and {today.year}"}, status=400)

        if employee_type == 'staff':
            if employee_id_raw.isdigit():
                employee = NonTeachingStaff.objects.filter(id=int(employee_id_raw)).first()
            else:
                employee = NonTeachingStaff.objects.filter(staff_id=employee_id_raw).first()
            if not employee:
                return Response({"error": "Staff not found"}, status=404)

            payments_qs = StaffSalaryPayment.objects.filter(
                staff=employee,
                year=year
            ).order_by('month')
            employee_identifier = employee.staff_id
            employee_name = employee.name
            employee_role = employee.role
            joining_date = employee.joining_date
            employee_profile_image = None
            if getattr(employee, 'profile_image', None):
                try:
                    employee_profile_image = request.build_absolute_uri(employee.profile_image.url)
                except Exception:
                    employee_profile_image = employee.profile_image.url
        else:
            if employee_id_raw.isdigit():
                employee = Teacher.objects.filter(id=int(employee_id_raw)).first()
            else:
                employee = Teacher.objects.filter(teacher_id=employee_id_raw).first()
            if not employee:
                return Response({"error": "Teacher not found"}, status=404)

            payments_qs = TeacherSalaryPayment.objects.filter(
                teacher=employee,
                year=year
            ).order_by('month')
            employee_identifier = employee.teacher_id
            employee_name = employee.name
            employee_role = "teacher"
            joining_date = employee.joining_date
            employee_profile_image = None
            if getattr(employee, 'profile_image', None):
                try:
                    employee_profile_image = request.build_absolute_uri(employee.profile_image.url)
                except Exception:
                    employee_profile_image = employee.profile_image.url

        payments_by_month = {int(p.month): p for p in payments_qs}

        # Build report only for applicable months:
        # 1) Never show future months of current year.
        # 2) Never show months before employee joining date.
        if joining_date and joining_date.year > year:
            return Response({
                "status": 200,
                "data": {
                    "employee_type": employee_type,
                    "employee_id": employee_identifier,
                    "employee_name": employee_name,
                    "employee_role": employee_role,
                    "employee_profile_image": employee_profile_image,
                    "joining_date": joining_date,
                    "year": year,
                    "monthly_report": [],
                    "summary": {
                        "months_with_records": 0,
                        "processed_count": 0,
                        "pending_count": 0,
                        "failed_count": 0,
                        "total_net_payable": 0.0,
                        "total_deduction": 0.0,
                    }
                }
            })

        start_month = 1
        if joining_date and joining_date.year == year:
            start_month = joining_date.month

        end_month = 12
        if year == today.year:
            end_month = today.month

        monthly_report = []
        total_net_payable = Decimal('0.00')
        total_deduction = Decimal('0.00')
        processed_count = 0
        pending_count = 0
        failed_count = 0

        for month in range(start_month, end_month + 1):
            payment = payments_by_month.get(month)
            if payment:
                net_payable = payment.net_payable or Decimal('0.00')
                total_net_payable += net_payable
                total_deduction += (payment.total_deduction or Decimal('0.00'))

                status_value = (payment.payment_status or 'pending').lower()
                if status_value == 'processed':
                    processed_count += 1
                elif status_value in {'failed', 'cancelled'}:
                    failed_count += 1
                else:
                    pending_count += 1

                monthly_report.append({
                    "month": month,
                    "month_name": month_name[month],
                    "has_record": True,
                    "payment_id": payment.id,
                    "payment_status": payment.payment_status,
                    "base_salary": float(payment.base_salary or 0),
                    "days_worked": int(payment.days_worked or 0),
                    "days_absent": int(payment.days_absent or 0),
                    "days_late": int(payment.days_late or 0),
                    "total_deduction": float(payment.total_deduction or 0),
                    "net_payable": float(net_payable),
                    "payment_date": payment.payment_date,
                    "transaction_id": payment.transaction_id,
                    "bank_reference": payment.bank_reference,
                })
            else:
                monthly_report.append({
                    "month": month,
                    "month_name": month_name[month],
                    "has_record": False,
                    "payment_id": None,
                    "payment_status": "not_processed",
                    "base_salary": 0.0,
                    "days_worked": 0,
                    "days_absent": 0,
                    "days_late": 0,
                    "total_deduction": 0.0,
                    "net_payable": 0.0,
                    "payment_date": None,
                    "transaction_id": None,
                    "bank_reference": None,
                })

        return Response({
            "status": 200,
            "data": {
                "employee_type": employee_type,
                "employee_id": employee_identifier,
                "employee_name": employee_name,
                "employee_role": employee_role,
                "employee_profile_image": employee_profile_image,
                "joining_date": joining_date,
                "year": year,
                "monthly_report": monthly_report,
                "summary": {
                    "months_with_records": len(payments_by_month),
                    "processed_count": processed_count,
                    "pending_count": pending_count,
                    "failed_count": failed_count,
                    "total_net_payable": float(total_net_payable),
                    "total_deduction": float(total_deduction),
                }
            }
        })


class AdminSalaryCardsOverviewView(APIView):
    """Card-level stats for salary admin dashboard."""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]

    def get(self, request):
        current_date = date.today()
        month, err = _parse_int(request.query_params.get('month', current_date.month), 'month', default=current_date.month)
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.query_params.get('year', current_date.year), 'year', default=current_date.year)
        if err:
            return Response({"error": err}, status=400)

        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)

        staff_structures_count = SalaryStructure.objects.aggregate(count=Count('id'))['count'] or 0
        teacher_structures_count = TeacherSalaryStructure.objects.aggregate(count=Count('id'))['count'] or 0

        staff_payments = StaffSalaryPayment.objects.filter(month=month, year=year)
        teacher_payments = TeacherSalaryPayment.objects.filter(month=month, year=year)

        staff_pending_count = staff_payments.filter(payment_status='pending').count()
        staff_processed_count = staff_payments.filter(payment_status='processed').count()
        staff_failed_count = staff_payments.filter(payment_status='failed').count()
        teacher_pending_count = teacher_payments.filter(payment_status='pending').count()
        teacher_processed_count = teacher_payments.filter(payment_status='processed').count()
        teacher_failed_count = teacher_payments.filter(payment_status='failed').count()
        pending_payments_count = staff_pending_count + teacher_pending_count
        processed_payments_count = staff_processed_count + teacher_processed_count
        failed_payments_count = staff_failed_count + teacher_failed_count

        staff_total_payable = staff_payments.aggregate(total=Sum('net_payable'))['total'] or Decimal('0.00')
        teacher_total_payable = teacher_payments.aggregate(total=Sum('net_payable'))['total'] or Decimal('0.00')
        total_payable_amount = staff_total_payable + teacher_total_payable

        return Response({
            "status": 200,
            "data": {
                "month": month,
                "year": year,
                "staff_structures_count": staff_structures_count,
                "teacher_structures_count": teacher_structures_count,
                "staff_pending_payments_count": staff_pending_count,
                "staff_processed_payments_count": staff_processed_count,
                "staff_failed_payments_count": staff_failed_count,
                "teacher_pending_payments_count": teacher_pending_count,
                "teacher_processed_payments_count": teacher_processed_count,
                "teacher_failed_payments_count": teacher_failed_count,
                "pending_payments_count": pending_payments_count,
                "processed_payments_count": processed_payments_count,
                "failed_payments_count": failed_payments_count,
                "staff_total_payable": float(staff_total_payable),
                "teacher_total_payable": float(teacher_total_payable),
                "total_payable_amount": float(total_payable_amount),
            }
        })


class AdminSalaryAuditLogsView(APIView):
    """Salary-specific audit logs with server-side filters."""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        days, valid_days = _parse_int_param(request.query_params.get('days', 90), default=90, min_value=1, max_value=365)
        page, valid_page = _parse_int_param(request.query_params.get('page', 1), default=1, min_value=1)
        page_size, valid_page_size = _parse_int_param(request.query_params.get('page_size', 120), default=120, min_value=1, max_value=200)
        if not (valid_days and valid_page and valid_page_size):
            return Response({"error": "Invalid pagination/filter values"}, status=400)

        month_raw = request.query_params.get('month')
        year_raw = request.query_params.get('year')
        status_filter = (request.query_params.get('status') or '').strip().lower()
        search_text = (request.query_params.get('q') or '').strip()

        month = None
        year = None
        if month_raw not in [None, '']:
            month, valid_month = _parse_int_param(month_raw, default=None, min_value=1, max_value=12)
            if not valid_month:
                return Response({"error": "month must be between 1 and 12"}, status=400)
        if year_raw not in [None, '']:
            year, valid_year = _parse_int_param(year_raw, default=None, min_value=2000, max_value=2100)
            if not valid_year:
                return Response({"error": "year must be between 2000 and 2100"}, status=400)

        since_date = timezone.now() - timedelta(days=days)
        logs = AuditLog.objects.filter(timestamp__gte=since_date).annotate(details_text=Cast('details', TextField()))

        # Keep this endpoint strictly salary scoped.
        logs = logs.filter(
            Q(details__module='salary') |
            Q(request_path__icontains='/salary/') |
            Q(action__icontains='SALARY') |
            Q(details_text__icontains='"module": "salary"')
        )

        if month is not None and year is not None:
            logs = logs.filter(
                Q(timestamp__month=month, timestamp__year=year) |
                Q(details__month=month, details__year=year) |
                Q(details__salary_month=month, details__salary_year=year) |
                Q(details__payment_month=month, details__payment_year=year)
            )
        elif month is not None:
            logs = logs.filter(
                Q(timestamp__month=month) |
                Q(details__month=month) |
                Q(details__salary_month=month) |
                Q(details__payment_month=month)
            )
        elif year is not None:
            logs = logs.filter(
                Q(timestamp__year=year) |
                Q(details__year=year) |
                Q(details__salary_year=year) |
                Q(details__payment_year=year)
            )

        if status_filter:
            logs = logs.filter(
                Q(details__payment_status=status_filter) |
                Q(details__current_status=status_filter) |
                Q(details__status=status_filter) |
                Q(details__transfer_status=status_filter) |
                Q(action__icontains=status_filter) |
                Q(details_text__icontains=f'"status": "{status_filter}"') |
                Q(details_text__icontains=f'"payment_status": "{status_filter}"')
            )

        if search_text:
            logs = logs.filter(
                Q(action__icontains=search_text) |
                Q(severity__icontains=search_text) |
                Q(request_path__icontains=search_text) |
                Q(details_text__icontains=search_text) |
                Q(user__email__icontains=search_text)
            )

        logs = logs.order_by('-timestamp')
        total = logs.count()
        start = (page - 1) * page_size
        end = start + page_size
        serializer = AuditLogSerializer(logs[start:end], many=True)

        return Response({
            "status": 200,
            "data": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "logs": serializer.data,
            }
        })


# ==========================================
# 6. BANK TRANSFER ENHANCED VIEWS
# ==========================================

class AdminProcessStaffSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Process staff salary with automatic bank transfer"""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]
    
    def post(self, request):
        # Validate request security
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "process_staff_with_bank"},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)
        
        staff_id = request.data.get('staff_id')
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        bank_code, bank_code_error = _resolve_razorpayx_bank_code(request.data.get('bank_code'))
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        
        staff = get_object_or_404(NonTeachingStaff, id=staff_id)
        
        # Check if payment exists
        existing_payment = StaffSalaryPayment.objects.filter(
            staff=staff, month=month, year=year
        ).first()
        
        if existing_payment:
            _log_salary_failure(
                request,
                failure_type='duplicate_payment',
                message=f"Payment already exists with status: {existing_payment.payment_status}",
                details={"operation": "process_staff_with_bank", "staff_id": staff.id, "payment_id": existing_payment.id},
            )
            return Response({
                "error": f"Payment already exists with status: {existing_payment.payment_status}"
            }, status=400)
        
        # Verify bank details
        if not all([staff.bank_account_number, staff.ifsc_code, staff.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Staff member missing bank details',
                details={"operation": "process_staff_with_bank", "staff_id": staff.id},
            )
            return Response({
                "error": "Staff member missing bank details. Please update bank information."
            }, status=400)
        
        # Get salary structure
        try:
            structure = SalaryStructure.objects.get(staff_type=staff.role)
        except SalaryStructure.DoesNotExist:
            _log_salary_failure(
                request,
                failure_type='salary_structure_missing',
                message='Salary structure not set for staff type',
                details={"operation": "process_staff_with_bank", "staff_id": staff.id, "staff_role": staff.role},
            )
            return Response({"error": "Salary structure not set for this staff type"}, 400)
        
        # Calculate salary
        try:
            calculation = AdminProcessStaffSalaryView()._calculate_salary(staff, month, year, structure)
        except ValueError as exc:
            _log_salary_failure(
                request,
                failure_type='salary_structure_inactive',
                message=str(exc),
                details={"operation": "process_staff_with_bank", "staff_id": staff.id, "staff_role": staff.role},
            )
            return Response({"error": str(exc)}, status=400)
        
        with transaction.atomic():
            # Create payment record
            payment = StaffSalaryPayment.objects.create(
                staff=staff,
                month=month,
                year=year,
                base_salary=calculation['base_salary'],
                per_day_wage=calculation['per_day_wage'],
                days_worked=calculation['days_worked'],
                days_absent=calculation['days_absent'],
                days_late=calculation['days_late'],
                absent_amount=calculation['absent_amount'],
                late_amount=calculation['late_amount'],
                total_deduction=calculation['total_deduction'],
                net_payable=calculation['net_payable'],
                transfer_bank_code=bank_code,
                payment_status='processing',
                created_by=request.user
            )
            
            # Initiate bank transfer
            bank_service = BankTransferService(bank_code)
            transfer_result = bank_service.initiate_transfer(
                {
                    'bank_account': staff.bank_account_number,
                    'ifsc_code': staff.ifsc_code,
                    'account_holder_name': staff.account_holder_name or staff.name,
                    'name': staff.name
                },
                calculation['net_payable'],
                f"Staff Salary - {staff.name} - {month}/{year}"
            )
            
            gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'
            
            if transfer_result.get('success'):
                payment.transaction_id = transfer_result.get('transaction_id')
                payment.bank_reference = transfer_result.get('bank_reference')
                
                # In dummy mode, mark as processed immediately
                if transfer_result.get('dummy_mode'):
                    payment.payment_status = 'processed'
                    payment.payment_date = timezone.now()
                else:
                    payment.payment_status = 'processing'
                    
                payment.save()
                
                # Log audit
                AuditLogger.log_action(
                    request.user,
                    'STAFF_SALARY_PROCESSED_WITH_BANK',
                    f"Processed salary for {staff.name}: {calculation['net_payable']} ({gateway_mode})",
                    request.META.get('REMOTE_ADDR'),
                    request
                )
                
                return Response({
                    "status": 200,
                    "message": f"Salary processed and bank transfer initiated ({gateway_mode} mode)",
                    "data": {
                        "payment_id": payment.id,
                        "staff_name": staff.name,
                        "net_payable": str(calculation['net_payable']),
                        "transaction_id": transfer_result.get('transaction_id'),
                        "bank_reference": transfer_result.get('bank_reference'),
                        "status": payment.payment_status,
                        "gateway_mode": gateway_mode
                    }
                })
            else:
                payment.payment_status = 'failed'
                payment.remarks = f"Bank transfer failed: {transfer_result.get('error')}"
                payment.save()
                _log_salary_failure(
                    request,
                    failure_type='bank_transfer_failed',
                    message=transfer_result.get('error') or 'Bank transfer failed',
                    details={
                        "operation": "process_staff_with_bank",
                        "payment_id": payment.id,
                        "staff_id": staff.id,
                        "bank_code": bank_code,
                        "gateway_mode": gateway_mode,
                    },
                )
                
                return Response({
                    "status": 400,
                    "error": "Bank transfer failed",
                    "details": transfer_result.get('error'),
                    "gateway_mode": gateway_mode
                }, status=400)


class AdminProcessTeacherSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Process teacher salary with automatic bank transfer"""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]
    
    def post(self, request):
        # Validate request security
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "process_teacher_with_bank"},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)
        
        teacher_id = request.data.get('teacher_id')
        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        bank_code, bank_code_error = _resolve_razorpayx_bank_code(request.data.get('bank_code'))
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        
        teacher = get_object_or_404(Teacher, id=teacher_id)
        
        # Check if payment exists
        existing_payment = TeacherSalaryPayment.objects.filter(
            teacher=teacher, month=month, year=year
        ).first()
        
        if existing_payment:
            _log_salary_failure(
                request,
                failure_type='duplicate_payment',
                message=f"Payment already exists with status: {existing_payment.payment_status}",
                details={"operation": "process_teacher_with_bank", "teacher_id": teacher.id, "payment_id": existing_payment.id},
            )
            return Response({
                "error": f"Payment already exists with status: {existing_payment.payment_status}"
            }, status=400)
        
        # Verify bank details
        if not all([teacher.bank_account_number, teacher.ifsc_code, teacher.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Teacher missing bank details',
                details={"operation": "process_teacher_with_bank", "teacher_id": teacher.id},
            )
            return Response({
                "error": "Teacher missing bank details. Please update bank information."
            }, status=400)
        
        # Get salary structure
        try:
            structure = TeacherSalaryStructure.objects.get(teacher_id=teacher.teacher_id)
        except TeacherSalaryStructure.DoesNotExist:
            _log_salary_failure(
                request,
                failure_type='salary_structure_missing',
                message='Salary structure not set for teacher',
                details={"operation": "process_teacher_with_bank", "teacher_id": teacher.id, "teacher_code": teacher.teacher_id},
            )
            return Response({"error": "Salary structure not set for this teacher"}, 400)
        
        # Calculate salary
        try:
            calculation = AdminProcessTeacherSalaryView()._calculate_salary(teacher, month, year, structure)
        except ValueError as exc:
            _log_salary_failure(
                request,
                failure_type='salary_structure_inactive',
                message=str(exc),
                details={"operation": "process_teacher_with_bank", "teacher_id": teacher.id, "teacher_code": teacher.teacher_id},
            )
            return Response({"error": str(exc)}, status=400)
        
        with transaction.atomic():
            # Create payment record
            payment = TeacherSalaryPayment.objects.create(
                teacher=teacher,
                month=month,
                year=year,
                base_salary=calculation['base_salary'],
                per_day_wage=calculation['per_day_wage'],
                days_worked=calculation['days_worked'],
                days_absent=calculation['days_absent'],
                days_late=calculation['days_late'],
                absent_amount=calculation['absent_amount'],
                late_amount=calculation['late_amount'],
                total_deduction=calculation['total_deduction'],
                net_payable=calculation['net_payable'],
                transfer_bank_code=bank_code,
                payment_status='processing',
                created_by=request.user
            )
            
            # Initiate bank transfer
            bank_service = BankTransferService(bank_code)
            transfer_result = bank_service.initiate_transfer(
                {
                    'bank_account': teacher.bank_account_number,
                    'ifsc_code': teacher.ifsc_code,
                    'account_holder_name': teacher.account_holder_name or teacher.name,
                    'name': teacher.name
                },
                calculation['net_payable'],
                f"Teacher Salary - {teacher.name} - {month}/{year}"
            )
            
            gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'
            
            if transfer_result.get('success'):
                payment.transaction_id = transfer_result.get('transaction_id')
                payment.bank_reference = transfer_result.get('bank_reference')
                
                if transfer_result.get('dummy_mode'):
                    payment.payment_status = 'processed'
                    payment.payment_date = timezone.now()
                else:
                    payment.payment_status = 'processing'
                    
                payment.save()
                
                AuditLogger.log_action(
                    request.user,
                    'TEACHER_SALARY_PROCESSED_WITH_BANK',
                    f"Processed salary for {teacher.name}: {calculation['net_payable']} ({gateway_mode})",
                    request.META.get('REMOTE_ADDR'),
                    request
                )
                
                return Response({
                    "status": 200,
                    "message": f"Salary processed and bank transfer initiated ({gateway_mode} mode)",
                    "data": {
                        "payment_id": payment.id,
                        "teacher_name": teacher.name,
                        "net_payable": str(calculation['net_payable']),
                        "transaction_id": transfer_result.get('transaction_id'),
                        "bank_reference": transfer_result.get('bank_reference'),
                        "status": payment.payment_status,
                        "gateway_mode": gateway_mode
                    }
                })
            else:
                payment.payment_status = 'failed'
                payment.remarks = f"Bank transfer failed: {transfer_result.get('error')}"
                payment.save()
                _log_salary_failure(
                    request,
                    failure_type='bank_transfer_failed',
                    message=transfer_result.get('error') or 'Bank transfer failed',
                    details={
                        "operation": "process_teacher_with_bank",
                        "payment_id": payment.id,
                        "teacher_id": teacher.id,
                        "bank_code": bank_code,
                        "gateway_mode": gateway_mode,
                    },
                )
                
                return Response({
                    "status": 400,
                    "error": "Bank transfer failed",
                    "details": transfer_result.get('error'),
                    "gateway_mode": gateway_mode
                }, status=400)


class AdminBulkProcessSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Bulk process salaries with bank transfers"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]
    
    def post(self, request):
        # Validate security
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "bulk_process_with_bank"},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)
        
        gateway_enabled = getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False)

        month, err = _parse_int(request.data.get('month'), 'month')
        if err:
            return Response({"error": err}, status=400)
        year, err = _parse_int(request.data.get('year'), 'year')
        if err:
            return Response({"error": err}, status=400)
        month_year_error = _validate_month_year(month, year)
        if month_year_error:
            return Response({"error": month_year_error}, status=400)
        future_period_error = _validate_not_future_period(month, year)
        if future_period_error:
            return Response({"error": future_period_error}, status=400)
        staff_type = request.data.get('staff_type')
        employee_type = request.data.get('employee_type', 'staff')  # 'staff' or 'teacher'
        bank_code, bank_code_error = _resolve_razorpayx_bank_code(request.data.get('bank_code'))
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        if employee_type not in ['staff', 'teacher']:
            _log_salary_failure(
                request,
                failure_type='invalid_request',
                message="employee_type must be either 'staff' or 'teacher'",
                details={"operation": "bulk_process_with_bank", "employee_type": employee_type},
            )
            return Response({"error": "employee_type must be either 'staff' or 'teacher'"}, status=400)
        
        # Get eligible employees
        if employee_type == 'staff':
            employees = NonTeachingStaff.objects.all()
            if staff_type:
                employees = employees.filter(role=staff_type)
            payment_model = StaffSalaryPayment
        else:
            employees = Teacher.objects.all()
            payment_model = TeacherSalaryPayment
        
        # Filter employees with bank details
        employees_with_bank = employees.exclude(
            bank_account_number__isnull=True
        ).exclude(
            bank_account_number=''
        ).exclude(
            ifsc_code__isnull=True
        ).exclude(
            ifsc_code=''
        )
        
        payment_records = []
        new_record_count = 0
        existing_record_count = 0
        
        for employee in employees_with_bank:
            existing_payment = payment_model.objects.filter(
                **{employee_type: employee}, month=month, year=year
            ).order_by('-id').first()

            # Process existing pending/failed records as part of bulk transfer.
            if existing_payment:
                if existing_payment.payment_status in ['processed', 'processing']:
                    continue
                existing_payment.transfer_bank_code = bank_code
                existing_payment.save(update_fields=['transfer_bank_code'])
                payment_records.append(existing_payment)
                existing_record_count += 1
                continue

            # No existing record: create a new payment record first.
            if employee_type == 'staff':
                try:
                    structure = SalaryStructure.objects.get(staff_type=employee.role)
                    calculation = AdminProcessStaffSalaryView()._calculate_salary(employee, month, year, structure)
                except SalaryStructure.DoesNotExist:
                    continue
                except ValueError:
                    continue
                payment = StaffSalaryPayment.objects.create(
                    staff=employee,
                    month=month,
                    year=year,
                    base_salary=calculation['base_salary'],
                    per_day_wage=calculation['per_day_wage'],
                    days_worked=calculation['days_worked'],
                    days_absent=calculation['days_absent'],
                    days_late=calculation['days_late'],
                    absent_amount=calculation['absent_amount'],
                    late_amount=calculation['late_amount'],
                    total_deduction=calculation['total_deduction'],
                    net_payable=calculation['net_payable'],
                    transfer_bank_code=bank_code,
                    payment_status='pending',
                    created_by=request.user
                )
            else:
                try:
                    structure = TeacherSalaryStructure.objects.get(teacher_id=employee.teacher_id)
                    calculation = AdminProcessTeacherSalaryView()._calculate_salary(employee, month, year, structure)
                except TeacherSalaryStructure.DoesNotExist:
                    continue
                except ValueError:
                    continue
                payment = TeacherSalaryPayment.objects.create(
                    teacher=employee,
                    month=month,
                    year=year,
                    base_salary=calculation['base_salary'],
                    per_day_wage=calculation['per_day_wage'],
                    days_worked=calculation['days_worked'],
                    days_absent=calculation['days_absent'],
                    days_late=calculation['days_late'],
                    absent_amount=calculation['absent_amount'],
                    late_amount=calculation['late_amount'],
                    total_deduction=calculation['total_deduction'],
                    net_payable=calculation['net_payable'],
                    transfer_bank_code=bank_code,
                    payment_status='pending',
                    created_by=request.user
                )
            payment_records.append(payment)
            new_record_count += 1
        
        if not payment_records:
            _log_salary_failure(
                request,
                failure_type='no_eligible_records',
                message='No eligible employees found for processing (processed/processing already handled)',
                details={"operation": "bulk_process_with_bank", "employee_type": employee_type, "month": month, "year": year},
                severity='WARNING',
                response_status=200,
            )
            return Response({
                "message": "No eligible pending/new employees found for transfer"
            })
        max_bulk = int(getattr(settings, 'MAX_BULK_PAYMENTS_PER_BATCH', 100))
        if len(payment_records) > max_bulk:
            _log_salary_failure(
                request,
                failure_type='bulk_limit_exceeded',
                message=f"Batch exceeds max limit ({max_bulk}). Eligible: {len(payment_records)}",
                details={"operation": "bulk_process_with_bank", "employee_type": employee_type, "max_bulk": max_bulk},
            )
            return Response({
                "error": f"Batch exceeds max limit ({max_bulk}). Eligible: {len(payment_records)}"
            }, status=400)
        
        # Process bank transfers
        processor = BulkPaymentProcessor(created_by=request.user)
        result = processor.process_bulk_payments(payment_records, bank_code)
        
        gateway_mode = 'DUMMY' if not gateway_enabled else 'REAL'
        
        # Log audit
        AuditLogger.log_action(
            request.user,
            f'BULK_{employee_type.upper()}_SALARY_PROCESSED',
            f"Processed {result['successful']} payments, Failed: {result['failed']} ({gateway_mode})",
            request.META.get('REMOTE_ADDR'),
            request
        )
        
        return Response({
            "status": 200,
            "message": f"Bulk processing completed ({gateway_mode} mode)",
            "data": {
                "batch_id": result.get('batch_id'),
                "total": result.get('total'),
                "successful": result.get('successful'),
                "failed": result.get('failed'),
                "new_records_created": new_record_count,
                "existing_pending_records": existing_record_count,
                "gateway_mode": gateway_mode,
                "results": result.get('results')
            }
        })


class AdminBulkProcessTeacherSalaryWithBankView(AdminBulkProcessSalaryWithBankView):
    """Bulk process teacher salaries with bank transfers"""
    def post(self, request):
        payload = request.data.copy()
        payload['employee_type'] = 'teacher'
        request._full_data = payload
        return super().post(request)


class AdminProcessExistingStaffSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Initiate bank transfer for an existing staff payment record."""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]

    def post(self, request, payment_id):
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "process_existing_staff_with_bank", "payment_id": payment_id},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)

        payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        if payment.payment_status in ['processed', 'processing']:
            _log_salary_failure(
                request,
                failure_type='invalid_payment_state',
                message=f"Payment already {payment.payment_status}",
                details={"operation": "process_existing_staff_with_bank", "payment_id": payment.id, "staff_id": payment.staff_id},
            )
            return Response({"error": f"Payment already {payment.payment_status}"}, status=400)

        staff = payment.staff
        if not all([staff.bank_account_number, staff.ifsc_code, staff.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Staff member missing bank details',
                details={"operation": "process_existing_staff_with_bank", "payment_id": payment.id, "staff_id": staff.id},
            )
            return Response({"error": "Staff member missing bank details. Please update bank information."}, status=400)

        bank_code, bank_code_error = _resolve_razorpayx_bank_code(
            request.data.get('bank_code') or getattr(payment, 'transfer_bank_code', RAZORPAYX_BANK_CODE) or RAZORPAYX_BANK_CODE
        )
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        bank_service = BankTransferService(bank_code)
        transfer_result = bank_service.initiate_transfer(
            {
                'bank_account': staff.bank_account_number,
                'ifsc_code': staff.ifsc_code,
                'account_holder_name': staff.account_holder_name or staff.name,
                'name': staff.name
            },
            payment.net_payable,
            f"Staff Salary - {staff.name} - {payment.month}/{payment.year}"
        )

        gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'
        payment.transfer_bank_code = bank_code

        if transfer_result.get('success'):
            payment.transaction_id = transfer_result.get('transaction_id')
            payment.bank_reference = transfer_result.get('bank_reference')
            payment.payment_status = 'processed' if transfer_result.get('dummy_mode') else 'processing'
            if transfer_result.get('dummy_mode'):
                payment.payment_date = timezone.now()
            payment.remarks = f"Bank transfer initiated via {bank_code} ({gateway_mode})"
            payment.save()

            AuditLogger.log_action(
                request.user,
                'STAFF_SALARY_PROCESSED_WITH_BANK',
                f"Processed existing payment with bank for {staff.name}: {payment.net_payable} ({gateway_mode})",
                request.META.get('REMOTE_ADDR'),
                request
            )

            return Response({
                "status": 200,
                "message": f"Salary transfer initiated ({gateway_mode} mode)",
                "data": {
                    "payment_id": payment.id,
                    "staff_name": staff.name,
                    "net_payable": str(payment.net_payable),
                    "transaction_id": payment.transaction_id,
                    "bank_reference": payment.bank_reference,
                    "status": payment.payment_status,
                    "gateway_mode": gateway_mode
                }
            })

        payment.payment_status = 'failed'
        payment.remarks = f"Bank transfer failed: {transfer_result.get('error')}"
        payment.save()
        _log_salary_failure(
            request,
            failure_type='bank_transfer_failed',
            message=transfer_result.get('error') or 'Bank transfer failed',
            details={"operation": "process_existing_staff_with_bank", "payment_id": payment.id, "staff_id": staff.id, "bank_code": bank_code},
        )
        return Response({
            "status": 400,
            "error": "Bank transfer failed",
            "details": transfer_result.get('error'),
            "gateway_mode": gateway_mode
        }, status=400)


class AdminProcessExistingTeacherSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Initiate bank transfer for an existing teacher payment record."""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]

    def post(self, request, payment_id):
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "process_existing_teacher_with_bank", "payment_id": payment_id},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)

        payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        if payment.payment_status in ['processed', 'processing']:
            _log_salary_failure(
                request,
                failure_type='invalid_payment_state',
                message=f"Payment already {payment.payment_status}",
                details={"operation": "process_existing_teacher_with_bank", "payment_id": payment.id, "teacher_id": payment.teacher_id},
            )
            return Response({"error": f"Payment already {payment.payment_status}"}, status=400)

        teacher = payment.teacher
        if not all([teacher.bank_account_number, teacher.ifsc_code, teacher.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Teacher missing bank details',
                details={"operation": "process_existing_teacher_with_bank", "payment_id": payment.id, "teacher_id": teacher.id},
            )
            return Response({"error": "Teacher missing bank details. Please update bank information."}, status=400)

        bank_code, bank_code_error = _resolve_razorpayx_bank_code(
            request.data.get('bank_code') or getattr(payment, 'transfer_bank_code', RAZORPAYX_BANK_CODE) or RAZORPAYX_BANK_CODE
        )
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        bank_service = BankTransferService(bank_code)
        transfer_result = bank_service.initiate_transfer(
            {
                'bank_account': teacher.bank_account_number,
                'ifsc_code': teacher.ifsc_code,
                'account_holder_name': teacher.account_holder_name or teacher.name,
                'name': teacher.name
            },
            payment.net_payable,
            f"Teacher Salary - {teacher.name} - {payment.month}/{payment.year}"
        )

        gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'
        payment.transfer_bank_code = bank_code

        if transfer_result.get('success'):
            payment.transaction_id = transfer_result.get('transaction_id')
            payment.bank_reference = transfer_result.get('bank_reference')
            payment.payment_status = 'processed' if transfer_result.get('dummy_mode') else 'processing'
            if transfer_result.get('dummy_mode'):
                payment.payment_date = timezone.now()
            payment.remarks = f"Bank transfer initiated via {bank_code} ({gateway_mode})"
            payment.save()

            AuditLogger.log_action(
                request.user,
                'TEACHER_SALARY_PROCESSED_WITH_BANK',
                f"Processed existing payment with bank for {teacher.name}: {payment.net_payable} ({gateway_mode})",
                request.META.get('REMOTE_ADDR'),
                request
            )

            return Response({
                "status": 200,
                "message": f"Salary transfer initiated ({gateway_mode} mode)",
                "data": {
                    "payment_id": payment.id,
                    "teacher_name": teacher.name,
                    "net_payable": str(payment.net_payable),
                    "transaction_id": payment.transaction_id,
                    "bank_reference": payment.bank_reference,
                    "status": payment.payment_status,
                    "gateway_mode": gateway_mode
                }
            })

        payment.payment_status = 'failed'
        payment.remarks = f"Bank transfer failed: {transfer_result.get('error')}"
        payment.save()
        _log_salary_failure(
            request,
            failure_type='bank_transfer_failed',
            message=transfer_result.get('error') or 'Bank transfer failed',
            details={"operation": "process_existing_teacher_with_bank", "payment_id": payment.id, "teacher_id": teacher.id, "bank_code": bank_code},
        )
        return Response({
            "status": 400,
            "error": "Bank transfer failed",
            "details": transfer_result.get('error'),
            "gateway_mode": gateway_mode
        }, status=400)


class AdminRetryStaffSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Retry a failed staff salary bank transfer using existing payment record."""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]

    def post(self, request, payment_id):
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "retry_staff_with_bank", "payment_id": payment_id},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)

        payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        if payment.payment_status != 'failed':
            _log_salary_failure(
                request,
                failure_type='invalid_payment_state',
                message='Only failed payments can be retried',
                details={"operation": "retry_staff_with_bank", "payment_id": payment.id, "staff_id": payment.staff_id},
            )
            return Response({"error": "Only failed payments can be retried"}, status=400)

        staff = payment.staff
        if not all([staff.bank_account_number, staff.ifsc_code, staff.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Staff member missing bank details',
                details={"operation": "retry_staff_with_bank", "payment_id": payment.id, "staff_id": staff.id},
            )
            return Response({"error": "Staff member missing bank details. Please update bank information."}, status=400)

        bank_code, bank_code_error = _resolve_razorpayx_bank_code(
            request.data.get('bank_code') or getattr(payment, 'transfer_bank_code', RAZORPAYX_BANK_CODE) or RAZORPAYX_BANK_CODE
        )
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        bank_service = BankTransferService(bank_code)
        transfer_result = bank_service.initiate_transfer(
            {
                'bank_account': staff.bank_account_number,
                'ifsc_code': staff.ifsc_code,
                'account_holder_name': staff.account_holder_name or staff.name,
                'name': staff.name
            },
            payment.net_payable,
            f"Staff Salary Retry - {staff.name} - {payment.month}/{payment.year}"
        )

        gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'

        if transfer_result.get('success'):
            payment.transaction_id = transfer_result.get('transaction_id')
            payment.bank_reference = transfer_result.get('bank_reference')
            payment.transfer_bank_code = bank_code
            payment.payment_status = 'processed' if transfer_result.get('dummy_mode') else 'processing'
            if transfer_result.get('dummy_mode'):
                payment.payment_date = timezone.now()
            payment.remarks = f"Retry initiated via {bank_code} ({gateway_mode})"
            payment.save()

            AuditLogger.log_action(
                request.user,
                'STAFF_SALARY_PROCESSED_WITH_BANK',
                f"Retried salary transfer for {staff.name}: {payment.net_payable} ({gateway_mode})",
                request.META.get('REMOTE_ADDR'),
                request
            )

            return Response({
                "status": 200,
                "message": f"Staff salary retry initiated ({gateway_mode} mode)",
                "data": {
                    "payment_id": payment.id,
                    "staff_name": staff.name,
                    "net_payable": str(payment.net_payable),
                    "transaction_id": payment.transaction_id,
                    "bank_reference": payment.bank_reference,
                    "status": payment.payment_status,
                    "gateway_mode": gateway_mode
                }
            })

        payment.transfer_bank_code = bank_code
        payment.payment_status = 'failed'
        payment.remarks = f"Bank transfer retry failed: {transfer_result.get('error')}"
        payment.save()
        _log_salary_failure(
            request,
            failure_type='bank_retry_failed',
            message=transfer_result.get('error') or 'Bank transfer retry failed',
            details={"operation": "retry_staff_with_bank", "payment_id": payment.id, "staff_id": staff.id, "bank_code": bank_code},
        )
        return Response({
            "status": 400,
            "error": "Bank transfer retry failed",
            "details": transfer_result.get('error'),
            "gateway_mode": gateway_mode
        }, status=400)


class AdminRetryTeacherSalaryWithBankView(APIView, PaymentSecurityMixin):
    """Retry a failed teacher salary bank transfer using existing payment record."""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]

    def post(self, request, payment_id):
        is_valid, message = self.validate_payment_request(request)
        if not is_valid:
            _log_salary_failure(
                request,
                failure_type='security_validation_failed',
                message=message,
                details={"operation": "retry_teacher_with_bank", "payment_id": payment_id},
                severity='SECURITY',
            )
            return Response({"error": message}, status=400)

        payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        if payment.payment_status != 'failed':
            _log_salary_failure(
                request,
                failure_type='invalid_payment_state',
                message='Only failed payments can be retried',
                details={"operation": "retry_teacher_with_bank", "payment_id": payment.id, "teacher_id": payment.teacher_id},
            )
            return Response({"error": "Only failed payments can be retried"}, status=400)

        teacher = payment.teacher
        if not all([teacher.bank_account_number, teacher.ifsc_code, teacher.account_holder_name]):
            _log_salary_failure(
                request,
                failure_type='bank_details_missing',
                message='Teacher missing bank details',
                details={"operation": "retry_teacher_with_bank", "payment_id": payment.id, "teacher_id": teacher.id},
            )
            return Response({"error": "Teacher missing bank details. Please update bank information."}, status=400)

        bank_code, bank_code_error = _resolve_razorpayx_bank_code(
            request.data.get('bank_code') or getattr(payment, 'transfer_bank_code', RAZORPAYX_BANK_CODE) or RAZORPAYX_BANK_CODE
        )
        if bank_code_error:
            return Response({"error": bank_code_error}, status=400)
        bank_service = BankTransferService(bank_code)
        transfer_result = bank_service.initiate_transfer(
            {
                'bank_account': teacher.bank_account_number,
                'ifsc_code': teacher.ifsc_code,
                'account_holder_name': teacher.account_holder_name or teacher.name,
                'name': teacher.name
            },
            payment.net_payable,
            f"Teacher Salary Retry - {teacher.name} - {payment.month}/{payment.year}"
        )

        gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'

        if transfer_result.get('success'):
            payment.transaction_id = transfer_result.get('transaction_id')
            payment.bank_reference = transfer_result.get('bank_reference')
            payment.transfer_bank_code = bank_code
            payment.payment_status = 'processed' if transfer_result.get('dummy_mode') else 'processing'
            if transfer_result.get('dummy_mode'):
                payment.payment_date = timezone.now()
            payment.remarks = f"Retry initiated via {bank_code} ({gateway_mode})"
            payment.save()

            AuditLogger.log_action(
                request.user,
                'TEACHER_SALARY_PROCESSED_WITH_BANK',
                f"Retried salary transfer for {teacher.name}: {payment.net_payable} ({gateway_mode})",
                request.META.get('REMOTE_ADDR'),
                request
            )

            return Response({
                "status": 200,
                "message": f"Teacher salary retry initiated ({gateway_mode} mode)",
                "data": {
                    "payment_id": payment.id,
                    "teacher_name": teacher.name,
                    "net_payable": str(payment.net_payable),
                    "transaction_id": payment.transaction_id,
                    "bank_reference": payment.bank_reference,
                    "status": payment.payment_status,
                    "gateway_mode": gateway_mode
                }
            })

        payment.transfer_bank_code = bank_code
        payment.payment_status = 'failed'
        payment.remarks = f"Bank transfer retry failed: {transfer_result.get('error')}"
        payment.save()
        _log_salary_failure(
            request,
            failure_type='bank_retry_failed',
            message=transfer_result.get('error') or 'Bank transfer retry failed',
            details={"operation": "retry_teacher_with_bank", "payment_id": payment.id, "teacher_id": teacher.id, "bank_code": bank_code},
        )
        return Response({
            "status": 400,
            "error": "Bank transfer retry failed",
            "details": transfer_result.get('error'),
            "gateway_mode": gateway_mode
        }, status=400)


class AdminVerifyBankTransferView(APIView):
    """Verify status of bank transfers"""
    permission_classes = [IsAuthenticated, EnhancedIsAdmin]
    
    def get(self, request, payment_id, payment_type):
        if payment_type == 'staff':
            payment = get_object_or_404(StaffSalaryPayment, id=payment_id)
        elif payment_type == 'teacher':
            payment = get_object_or_404(TeacherSalaryPayment, id=payment_id)
        else:
            _log_salary_failure(
                request,
                failure_type='invalid_request',
                message="payment_type must be 'staff' or 'teacher'",
                details={"operation": "verify_bank_transfer", "payment_id": payment_id, "payment_type": payment_type},
            )
            return Response({"error": "payment_type must be 'staff' or 'teacher'"}, status=400)
        
        if not payment.transaction_id:
            _log_salary_failure(
                request,
                failure_type='missing_transaction',
                message='No transaction found for this payment',
                details={"operation": "verify_bank_transfer", "payment_id": payment.id, "payment_type": payment_type},
            )
            return Response({
                "error": "No transaction found for this payment"
            }, status=400)
        
        # Check transfer status
        bank_service = BankTransferService(getattr(payment, 'transfer_bank_code', RAZORPAYX_BANK_CODE) or RAZORPAYX_BANK_CODE)
        status = bank_service.check_transfer_status(payment.transaction_id)
        
        gateway_mode = 'DUMMY' if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'REAL'
        
        # Update payment status if needed
        if status.get('status') == 'completed' and payment.payment_status != 'processed':
            payment.payment_status = 'processed'
            payment.payment_date = timezone.now()
            payment.save()
        elif status.get('status') == 'failed' and payment.payment_status == 'processing':
            payment.payment_status = 'failed'
            payment.save()

        AuditLogger.log_action(
            request.user,
            'BANK_TRANSFER_VERIFIED',
            {
                "module": "salary",
                "entity": f"{payment_type}_payment",
                "payment_id": payment.id,
                "payment_type": payment_type,
                "staff_id": payment.staff_id if payment_type == 'staff' else None,
                "teacher_id": payment.teacher_id if payment_type == 'teacher' else None,
                "staff_name": payment.staff.name if payment_type == 'staff' and getattr(payment, 'staff', None) else None,
                "teacher_name": payment.teacher.name if payment_type == 'teacher' and getattr(payment, 'teacher', None) else None,
                "credited_to": (
                    payment.staff.name if payment_type == 'staff' and getattr(payment, 'staff', None)
                    else payment.teacher.name if payment_type == 'teacher' and getattr(payment, 'teacher', None)
                    else None
                ),
                "credited_to_type": payment_type,
                "transaction_id": payment.transaction_id,
                "payment_status": payment.payment_status,
                "bank_status": status.get('status'),
                "gateway_mode": gateway_mode,
                "operation": "verify_bank_transfer",
            },
            request.META.get('REMOTE_ADDR'),
            request=request,
            response_status=200,
        )
        
        return Response({
            "status": 200,
            "data": {
                "payment_id": payment.id,
                "payment_type": payment_type,
                "transaction_id": payment.transaction_id,
                "bank_reference": payment.bank_reference,
                "current_status": payment.payment_status,
                "bank_status": status,
                "gateway_mode": gateway_mode
            }
        })


def _mask_email(email: str) -> str:
    if not email or '@' not in email:
        return "configured email"
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = local[0] + '*'
    else:
        masked_local = local[0] + ('*' * (len(local) - 2)) + local[-1]
    return f"{masked_local}@{domain}"


def _salary_transfer_otp_key(user_id: int) -> str:
    return f"salary_transfer_otp_{user_id}"


class AdminSalaryTransferOtpSendView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        recipient_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
        if not recipient_email:
            return Response({"error": "DEFAULT_FROM_EMAIL is not configured"}, status=400)

        cache.delete(_salary_transfer_otp_verified_key(request.user.id))
        otp = f"{random.randint(100000, 999999)}"
        cache.set(_salary_transfer_otp_key(request.user.id), otp, timeout=30)

        try:
            send_mail(
                subject="Salary Transfer OTP",
                message=f"Your salary transfer OTP is {otp}. It is valid for 30 seconds.",
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[recipient_email],
                fail_silently=False,
            )
        except Exception as exc:
            return Response({"error": f"Failed to send OTP email: {str(exc)}"}, status=500)

        return Response({
            "status": 200,
            "message": f"OTP sent to {_mask_email(recipient_email)}",
            "expires_in": 30,
        })


class AdminSalaryTransferOtpResendView(AdminSalaryTransferOtpSendView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]


class AdminSalaryTransferOtpVerifyView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def post(self, request):
        otp_input = str(request.data.get('otp', '')).strip()
        if not otp_input:
            return Response({"error": "OTP is required"}, status=400)

        cached_otp = cache.get(_salary_transfer_otp_key(request.user.id))
        if not cached_otp:
            return Response({"error": "OTP expired or not requested"}, status=400)

        if str(cached_otp) != otp_input:
            return Response({"error": "Invalid OTP"}, status=400)

        cache.delete(_salary_transfer_otp_key(request.user.id))
        cache.set(_salary_transfer_otp_verified_key(request.user.id), True, timeout=30)
        return Response({
            "status": 200,
            "message": "OTP verified successfully",
            "verified": True,
        })


#flutter views
class AdminStaffSalaryMobileListView(APIView):
    """Lightweight list view for mobile app (Custom ID and Name only, original sorting)"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        status = request.query_params.get('status')

        # Using .all() just like the existing view to guarantee the exact same order
        payments = StaffSalaryPayment.objects.select_related('staff').all()
        
        if month:
            payments = payments.filter(month=month)
        if year:
            payments = payments.filter(year=year)
        if status:
            payments = payments.filter(payment_status=status)

        data = [{
            "payment_id": p.id, 
            "staff_id": p.staff.staff_id,
            "name": p.staff.name
        } for p in payments]
        
        return Response({"status": 200, "data": data})


class AdminTeacherSalaryMobileListView(APIView):
    """Lightweight list view for mobile app (Custom ID and Name only, original sorting)"""
    permission_classes = [IsAuthenticated, IsOwnerAdminOrFinanceAdminStaff]

    def get(self, request):
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        status = request.query_params.get('status')

        # Using .all() just like the existing view to guarantee the exact same order
        payments = TeacherSalaryPayment.objects.select_related('teacher').all()
        
        if month:
            payments = payments.filter(month=month)
        if year:
            payments = payments.filter(year=year)
        if status:
            payments = payments.filter(payment_status=status)

        data = [{
            "payment_id": p.id, 
            "teacher_id": p.teacher.teacher_id,
            "name": p.teacher.name
        } for p in payments]
        
        return Response({"status": 200, "data": data})