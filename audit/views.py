# audit/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import BasePermission
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import timedelta
import json
from .models import AuditLog
from .serializers import AuditLogSerializer


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


PAYMENT_AUDIT_ACTIONS = {
    'PAYMENT_INITIATED',
    'PAYMENT_PROCESSED',
    'PAYMENT_FAILED',
    'PAYMENT_VERIFIED',
    'BULK_PAYMENT',
    'BANK_TRANSFER',
    'BANK_TRANSFER_VERIFIED',
    'STAFF_SALARY_PROCESSED',
    'TEACHER_SALARY_PROCESSED',
    'STAFF_SALARY_PROCESSED_WITH_BANK',
    'TEACHER_SALARY_PROCESSED_WITH_BANK',
    'BULK_STAFF_SALARY_PROCESSED',
    'BULK_TEACHER_SALARY_PROCESSED',
}

SALARY_PAYMENT_AUDIT_ACTIONS = {
    'PAYMENT_INITIATED',
    'PAYMENT_PROCESSED',
    'PAYMENT_FAILED',
    'PAYMENT_VERIFIED',
    'BANK_TRANSFER',
    'BANK_TRANSFER_VERIFIED',
    'STAFF_SALARY_PROCESSED',
    'TEACHER_SALARY_PROCESSED',
    'STAFF_SALARY_PROCESSED_WITH_BANK',
    'TEACHER_SALARY_PROCESSED_WITH_BANK',
    'BULK_STAFF_SALARY_PROCESSED',
    'BULK_TEACHER_SALARY_PROCESSED',
}

FEES_PAYMENT_AUDIT_ACTIONS = {
    'PAYMENT_INITIATED',
    'PAYMENT_PROCESSED',
    'PAYMENT_FAILED',
    'PAYMENT_VERIFIED',
}


class IsAdminAuditUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(user.is_superuser or user.user_type in ('admin', 'super_admin'))


class IsFinanceOrAdminStaffAuditUser(BasePermission):
    ALLOWED_STAFF_ROLES = {'admin_staff', 'finance_staff'}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.user_type != 'staff' or not hasattr(user, 'staff_profile'):
            return False
        return getattr(user.staff_profile, 'role', None) in self.ALLOWED_STAFF_ROLES


class AuditLogListView(APIView):
    """Admin view: all audit logs with filters"""
    permission_classes = [IsAuthenticated, IsAdminAuditUser]
    MAX_GENERIC_DETAILS_SEARCH_DAYS = 30

    def get_base_queryset(self):
        return AuditLog.objects.all()

    def get_allowed_actions(self):
        return {choice[0] for choice in AuditLog.ACTION_TYPES}
    
    def get(self, request):
        # Get query parameters
        user_id = request.query_params.get('user_id')
        action = request.query_params.get('action')
        severity = request.query_params.get('severity')
        days_raw = request.query_params.get('days', 7)
        gateway_mode = request.query_params.get('gateway_mode')
        date_from_raw = request.query_params.get('date_from')
        date_to_raw = request.query_params.get('date_to')
        academic_year = request.query_params.get('academic_year')
        search_text = (request.query_params.get('q') or '').strip()
        scope = (request.query_params.get('scope') or '').strip().lower()
        
        # Base queryset
        logs = self.get_base_queryset()
        window_days = None

        valid_actions = self.get_allowed_actions()
        valid_severities = {choice[0] for choice in AuditLog.SEVERITY_LEVELS}
        
        # Apply filters
        if user_id:
            logs = logs.filter(user_id=user_id)
        if action:
            if action not in valid_actions:
                return Response({"error": "Invalid 'action' filter."}, status=400)
            logs = logs.filter(action=action)
        if severity:
            if severity not in valid_severities:
                return Response({"error": "Invalid 'severity' filter."}, status=400)
            logs = logs.filter(severity=severity)
        if gateway_mode:
            gateway_mode = gateway_mode.upper()
            if gateway_mode not in ['REAL', 'DUMMY']:
                return Response({"error": "Invalid 'gateway_mode'. Use REAL or DUMMY."}, status=400)
            logs = logs.filter(gateway_mode=gateway_mode)

        # Date filtering: date_from/date_to take precedence, else fallback to days window.
        if date_from_raw or date_to_raw:
            if not (date_from_raw and date_to_raw):
                return Response({"error": "Provide both 'date_from' and 'date_to' in YYYY-MM-DD format."}, status=400)
            date_from = parse_date(date_from_raw)
            date_to = parse_date(date_to_raw)
            if not date_from or not date_to:
                return Response({"error": "Invalid date range. Use YYYY-MM-DD format."}, status=400)
            if date_from > date_to:
                return Response({"error": "'date_from' cannot be after 'date_to'."}, status=400)
            logs = logs.filter(timestamp__date__gte=date_from, timestamp__date__lte=date_to)
            window_days = (date_to - date_from).days + 1
        else:
            days, is_valid_days = _parse_int_param(days_raw, default=7, min_value=1, max_value=365)
            if not is_valid_days:
                return Response({"error": "Invalid 'days'. Must be an integer between 1 and 365."}, status=400)
            since_date = timezone.now() - timedelta(days=days)
            logs = logs.filter(timestamp__gte=since_date)
            window_days = days

        if academic_year:
            logs = logs.filter(Q(details__academic_year=academic_year))

        if scope:
            if scope == 'fees':
                logs = logs.filter(
                    Q(details__module__iexact='fees')
                    | Q(details__operation__icontains='fee')
                    | Q(details__operation__icontains='payment')
                    | Q(request_path__icontains='/api/fees/')
                )
            else:
                return Response({"error": "Invalid 'scope'. Supported values: fees."}, status=400)

        if search_text:
            search_filters = (
                Q(action__icontains=search_text) |
                Q(severity__icontains=search_text) |
                Q(request_method__icontains=search_text) |
                Q(request_path__icontains=search_text) |
                Q(user__email__icontains=search_text) |
                Q(details__transaction_id__icontains=search_text) |
                Q(details__student_id__icontains=search_text)
            )
            # Generic JSON text search is expensive; allow only on a small time window.
            if window_days is not None and window_days <= self.MAX_GENERIC_DETAILS_SEARCH_DAYS:
                search_filters = search_filters | Q(details__icontains=search_text)
            logs = logs.filter(search_filters)
        
        # Pagination
        page, is_valid_page = _parse_int_param(request.query_params.get('page', 1), default=1, min_value=1)
        page_size, is_valid_page_size = _parse_int_param(
            request.query_params.get('page_size', 50),
            default=50,
            min_value=1,
            max_value=200
        )
        if not (is_valid_page and is_valid_page_size):
            return Response({"error": "Invalid pagination. page >= 1 and page_size between 1 and 200."}, status=400)
        start = (page - 1) * page_size
        end = start + page_size
        
        total = logs.count()
        paginated_logs = logs[start:end]
        
        serializer = AuditLogSerializer(paginated_logs, many=True)
        
        return Response({
            "status": 200,
            "data": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "logs": serializer.data
            }
        })


class AuditLogDetailView(APIView):
    """Admin view: single audit log entry"""
    permission_classes = [IsAuthenticated, IsAdminAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.all()
    
    def get(self, request, log_id):
        log = get_object_or_404(self.get_base_queryset(), id=log_id)
        serializer = AuditLogSerializer(log)
        return Response({"status": 200, "data": serializer.data})


class AuditLogSummaryView(APIView):
    """Admin view: summary statistics for all audit logs"""
    permission_classes = [IsAuthenticated, IsAdminAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.all()
    
    def get(self, request):
        days, is_valid_days = _parse_int_param(request.query_params.get('days', 7), default=7, min_value=1, max_value=365)
        if not is_valid_days:
            return Response({"error": "Invalid 'days'. Must be an integer between 1 and 365."}, status=400)
        since_date = timezone.now() - timedelta(days=days)
        
        logs = self.get_base_queryset().filter(timestamp__gte=since_date)
        
        # Summary statistics
        total_logs = logs.count()
        
        # Action counts
        action_counts = logs.values('action').annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Severity counts
        severity_counts = logs.values('severity').annotate(
            count=Count('id')
        )
        
        # Gateway mode distribution
        gateway_counts = logs.values('gateway_mode').annotate(
            count=Count('id')
        )
        
        # User activity
        user_activity = logs.values('user__email').annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Daily activity for chart
        daily_rows = logs.annotate(day=TruncDate('timestamp')).values('day').annotate(count=Count('id'))
        daily_map = {row['day'].isoformat(): row['count'] for row in daily_rows if row.get('day')}
        daily_activity = []
        for i in range(days):
            day = timezone.localdate() - timedelta(days=i)
            key = day.isoformat()
            daily_activity.append({
                'date': key,
                'count': daily_map.get(key, 0)
            })
        
        return Response({
            "status": 200,
            "data": {
                "period_days": days,
                "total_logs": total_logs,
                "action_counts": action_counts,
                "severity_counts": severity_counts,
                "gateway_distribution": gateway_counts,
                "top_users": user_activity,
                "daily_activity": daily_activity
            }
        })


class AuditLogExportView(APIView):
    """Admin view: export all audit logs as CSV"""
    permission_classes = [IsAuthenticated, IsAdminAuditUser]
    MAX_EXPORT_LIMIT = 10000

    def get_base_queryset(self):
        return AuditLog.objects.all()

    def export_scope_name(self):
        return 'audit_logs'
    
    def get(self, request):
        import csv
        from django.http import HttpResponse
        
        # Get filters (similar to list view)
        days, is_valid_days = _parse_int_param(request.query_params.get('days', 30), default=30, min_value=1, max_value=365)
        if not is_valid_days:
            return Response({"error": "Invalid 'days'. Must be an integer between 1 and 365."}, status=400)
        since_date = timezone.now() - timedelta(days=days)
        
        logs = self.get_base_queryset().filter(timestamp__gte=since_date).select_related('user')
        total_matching = logs.count()
        logs = logs[:self.MAX_EXPORT_LIMIT]

        AuditLog.log(
            user=request.user,
            action='EXPORT_DATA',
            severity='SECURITY',
            details={
                'type': self.export_scope_name(),
                'days': days,
                'max_export_limit': self.MAX_EXPORT_LIMIT,
                'total_matching': total_matching,
            },
            request=request,
        )
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="audit_logs_{timezone.now().date()}.csv"'
        response['X-Export-Limited'] = 'true' if total_matching > self.MAX_EXPORT_LIMIT else 'false'
        response['X-Export-Total-Matching'] = str(total_matching)
        response['X-Export-Limit'] = str(self.MAX_EXPORT_LIMIT)
        
        writer = csv.writer(response)
        writer.writerow(['Timestamp', 'User', 'Action', 'Severity', 'IP Address', 
                        'Gateway Mode', 'Request Method', 'Request Path', 'Details'])
        
        for log in logs:
            writer.writerow([
                log.timestamp,
                log.user.email if log.user else 'System',
                log.action,
                log.severity,
                log.ip_address,
                log.gateway_mode,
                log.request_method,
                log.request_path,
                json.dumps(log.details or {}, ensure_ascii=False)
            ])
        
        return response


class StaffPaymentAuditLogListView(AuditLogListView):
    """Staff (admin_staff/finance_staff) view: payment-related audit logs only"""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(action__in=PAYMENT_AUDIT_ACTIONS)

    def get_allowed_actions(self):
        return PAYMENT_AUDIT_ACTIONS


class StaffPaymentAuditLogDetailView(AuditLogDetailView):
    """Staff (admin_staff/finance_staff) view: payment-related audit log detail only"""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(action__in=PAYMENT_AUDIT_ACTIONS)


class StaffPaymentAuditLogSummaryView(AuditLogSummaryView):
    """Staff (admin_staff/finance_staff) view: payment-related audit summary only"""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(action__in=PAYMENT_AUDIT_ACTIONS)


class StaffPaymentAuditLogExportView(AuditLogExportView):
    """Staff (admin_staff/finance_staff) view: payment-related audit export only"""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(action__in=PAYMENT_AUDIT_ACTIONS)

    def export_scope_name(self):
        return 'payment_audit_logs'


class StaffSalaryPaymentAuditLogListView(AuditLogListView):
    """Staff (admin_staff/finance_staff): salary payment logs only (no create logs)."""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(
            action__in=SALARY_PAYMENT_AUDIT_ACTIONS
        ).filter(
            Q(details__module='salary') | Q(request_path__icontains='/salary/')
        )

    def get_allowed_actions(self):
        return SALARY_PAYMENT_AUDIT_ACTIONS


class StaffFeesPaymentAuditLogListView(AuditLogListView):
    """Staff (admin_staff/finance_staff): fees payment logs only (no create logs)."""
    permission_classes = [IsAuthenticated, IsFinanceOrAdminStaffAuditUser]

    def get_base_queryset(self):
        return AuditLog.objects.filter(
            action__in=FEES_PAYMENT_AUDIT_ACTIONS
        ).filter(
            Q(details__module='fees') | Q(request_path__icontains='/fees/')
        )

    def get_allowed_actions(self):
        return FEES_PAYMENT_AUDIT_ACTIONS
