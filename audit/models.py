# audit/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
import ipaddress
import importlib
import logging

logger = logging.getLogger(__name__)

class AuditLog(models.Model):
    """
    Comprehensive audit logging for all sensitive operations
    """
    ACTION_TYPES = (
        # Authentication Actions
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('LOGIN_FAILED', 'Login Failed'),
        ('PASSWORD_CHANGE', 'Password Change'),
        ('PASSWORD_RESET', 'Password Reset'),
        
        # CRUD Operations
        ('CREATE', 'Create'),
        ('READ', 'Read'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        
        # Payment Actions
        ('PAYMENT_INITIATED', 'Payment Initiated'),
        ('PAYMENT_PROCESSED', 'Payment Processed'),
        ('PAYMENT_FAILED', 'Payment Failed'),
        ('PAYMENT_VERIFIED', 'Payment Verified'),
        ('BULK_PAYMENT', 'Bulk Payment'),
        ('BANK_TRANSFER', 'Bank Transfer'),
        ('BANK_TRANSFER_VERIFIED', 'Bank Transfer Verified'),
        
        # Staff/Teacher Specific
        ('STAFF_SALARY_CALCULATED', 'Staff Salary Calculated'),
        ('TEACHER_SALARY_CALCULATED', 'Teacher Salary Calculated'),
        ('STAFF_SALARY_PROCESSED', 'Staff Salary Processed'),
        ('TEACHER_SALARY_PROCESSED', 'Teacher Salary Processed'),
        ('STAFF_SALARY_PROCESSED_WITH_BANK', 'Staff Salary Processed with Bank'),
        ('TEACHER_SALARY_PROCESSED_WITH_BANK', 'Teacher Salary Processed with Bank'),
        ('BULK_STAFF_SALARY_PROCESSED', 'Bulk Staff Salary Processed'),
        ('BULK_TEACHER_SALARY_PROCESSED', 'Bulk Teacher Salary Processed'),
        
        # Settings Changes
        ('SALARY_STRUCTURE_CREATED', 'Salary Structure Created'),
        ('SALARY_STRUCTURE_UPDATED', 'Salary Structure Updated'),
        ('SALARY_STRUCTURE_DELETED', 'Salary Structure Deleted'),
        
        # Security Actions
        ('PERMISSION_CHANGE', 'Permission Change'),
        ('ROLE_CHANGE', 'Role Change'),
        ('TWO_FACTOR_ENABLED', '2FA Enabled'),
        ('TWO_FACTOR_DISABLED', '2FA Disabled'),
        ('ADMIN_VERIFICATION', 'Admin Verification'),
        ('BULK_APPROVAL_TOKEN', 'Bulk Approval Token Generated'),
        
        # System Actions
        ('SYSTEM_ERROR', 'System Error'),
        ('API_CALL', 'API Call'),
        ('EXPORT_DATA', 'Data Export'),
        ('IMPORT_DATA', 'Data Import'),
    )
    
    SEVERITY_LEVELS = (
        ('INFO', 'Information'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
        ('CRITICAL', 'Critical'),
        ('SECURITY', 'Security'),
    )
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=50, choices=ACTION_TYPES)
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='INFO')
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    response_status = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Additional tracking
    gateway_mode = models.CharField(max_length=10, blank=True, help_text='REAL or DUMMY mode')
    session_key = models.CharField(max_length=100, blank=True)
    browser_info = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
            models.Index(fields=['ip_address', 'timestamp']),
            models.Index(fields=['severity', 'timestamp']),
            models.Index(fields=['gateway_mode', 'timestamp']),
            models.Index(fields=['timestamp']),
        ]
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        
    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.action}"
    
    @staticmethod
    def _gateway_mode_from_settings():
        if getattr(settings, 'RAZORPAY_ENABLED', False):
            return 'REAL'
        if getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False):
            return 'REAL'
        return 'DUMMY'

    def save(self, *args, **kwargs):
        # Auto-set gateway mode from settings
        if not self.gateway_mode:
            self.gateway_mode = self._gateway_mode_from_settings()
        super().save(*args, **kwargs)
    
    @classmethod
    def log(cls, user, action, details=None, request=None, severity='INFO', **kwargs):
        """
        Convenience method to create audit log entries
        """
        ip_address = None
        user_agent = ''
        request_method = ''
        request_path = ''
        
        if request:
            ip_address = cls._get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            request_method = request.method
            request_path = request.path
        
        details_payload = details or {}
        explicit_gateway_mode = kwargs.pop('gateway_mode', None)
        gateway_mode = explicit_gateway_mode or details_payload.get('gateway_mode') or cls._gateway_mode_from_settings()

        payload = {
            'user': user,
            'action': action,
            'severity': severity,
            'details': details_payload,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'request_method': request_method,
            'request_path': request_path,
            'gateway_mode': gateway_mode,
            **kwargs
        }

        try:
            # Optional async hook for higher-throughput deployments.
            # Settings:
            # - AUDIT_LOG_ASYNC_ENABLED=True
            # - AUDIT_LOG_ASYNC_HANDLER='path.to.callable'
            # Callable must accept one positional arg: payload dict.
            if getattr(settings, 'AUDIT_LOG_ASYNC_ENABLED', False):
                handler_path = getattr(settings, 'AUDIT_LOG_ASYNC_HANDLER', '')
                if handler_path:
                    module_name, func_name = handler_path.rsplit('.', 1)
                    handler = getattr(importlib.import_module(module_name), func_name)
                    handler(payload)
                    return None

            return cls.objects.create(**payload)
        except Exception:
            # Never block core business flow if audit write fails.
            logger.exception("Audit logging failed for action=%s", action)
            return None
    
    @staticmethod
    def _get_client_ip(request):
        """
        Get client IP address from request.
        Only trust X-Forwarded-For when request comes via trusted proxy.
        """
        remote_addr = request.META.get('REMOTE_ADDR', '')
        trusted_proxies = set(getattr(settings, 'AUDIT_TRUSTED_PROXY_IPS', []))
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        x_real_ip = request.META.get('HTTP_X_REAL_IP', '')

        candidate = remote_addr
        if remote_addr in trusted_proxies:
            if x_forwarded_for:
                candidate = x_forwarded_for.split(',')[0].strip()
            elif x_real_ip:
                candidate = x_real_ip.strip()

        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return remote_addr or None
