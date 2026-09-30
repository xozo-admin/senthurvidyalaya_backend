# salary/security.py
from rest_framework.permissions import BasePermission
from django.core.cache import cache
import hashlib
import hmac
import time
from django.conf import settings

ADMIN_USER_TYPES = {'admin', 'super_admin'}

class EnhancedIsAdmin(BasePermission):
    """
    Enhanced admin permission with additional security checks
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_type = getattr(request.user, 'user_type', None)
        is_owner_admin = user_type in ADMIN_USER_TYPES or request.user.is_superuser
        staff_role = getattr(getattr(request.user, 'staff_profile', None), 'role', None)
        is_finance_admin_staff = user_type == 'staff' and staff_role in {'admin_staff', 'finance_staff'}

        # Allow owner admin or admin/finance staff roles
        if not (is_owner_admin or is_finance_admin_staff):
            return False
            
        # Additional security: Check if admin session is verified
        session_key = f"admin_verified_{request.user.id}"
        
        # For payment operations, always require verification
        if 'payment' in request.path and request.method in ['POST', 'PUT', 'DELETE']:
            if not cache.get(session_key) and not settings.DEBUG:
                if not request.headers.get('X-Admin-Verified'):
                    return False
        
        return True


class PaymentSecurityMixin:
    """
    Mixin for payment-related security
    """
    def validate_payment_request(self, request):
        # Skip validation in dummy mode if configured
        if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False):
            return True, "Dummy mode - validation skipped"

        # Industry-style approach: browser/client does not sign financial requests.
        # Signature verification is optional and only enforced when explicitly enabled.
        require_client_signature = getattr(settings, 'PAYMENT_CLIENT_SIGNATURE_REQUIRED', False)

        # Timestamp is mandatory only when client signature mode is enabled.
        timestamp = request.headers.get('X-Timestamp')
        if require_client_signature and not timestamp:
            return False, "Missing timestamp"

        if timestamp:
            try:
                if abs(time.time() - float(timestamp)) > 300:  # 5 minutes
                    return False, "Request expired"
            except ValueError:
                return False, "Invalid timestamp format"

        if request.method != 'GET':
            signature = request.headers.get('X-Signature')
            if require_client_signature and not signature:
                return False, "Missing signature"

            # If a signature is provided, validate it.
            if signature:
                if not timestamp:
                    return False, "Missing timestamp"

                signing_secret = getattr(settings, 'PAYMENT_REQUEST_SIGNING_SECRET', settings.SECRET_KEY)
                body_hash = hashlib.sha256(request.body or b'').hexdigest()
                payload = f"{request.method}\n{request.path}\n{timestamp}\n{request.user.id}\n{body_hash}"
                expected = hmac.new(
                    signing_secret.encode(),
                    payload.encode(),
                    hashlib.sha256
                ).hexdigest()

                if not hmac.compare_digest(signature, expected):
                    return False, "Invalid signature"

        return True, "Valid"


# salary/security.py - Update the AuditLogger class

class AuditLogger:
    """
    Log all sensitive operations using the AuditLog model
    """
    @staticmethod
    def log_action(user, action, details, ip_address, request=None, severity=None, response_status=None):
        """
        Log action to database using AuditLog model
        """
        from audit.models import AuditLog
        
        gateway_mode = 'REAL' if getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'DUMMY'
        
        # Determine severity based on action unless explicitly provided.
        derived_severity = 'INFO'
        if any(word in action for word in ['FAILED', 'ERROR']):
            derived_severity = 'ERROR'
        elif any(word in action for word in ['DELETE', 'CRITICAL']):
            derived_severity = 'CRITICAL'
        elif any(word in action for word in ['BULK', 'PROCESSED']):
            derived_severity = 'WARNING'

        if severity:
            derived_severity = severity

        # Ensure audit payload is always structured JSON for reliable filtering/reporting.
        normalized_details = details if isinstance(details, dict) else {"message": str(details)}
        normalized_details.setdefault("module", "salary")
        if user is not None:
            actor_name = getattr(user, "username", None) or str(user)
            normalized_details.setdefault("performed_by", actor_name)
            normalized_details.setdefault("performed_by_user_id", getattr(user, "id", None))
            normalized_details.setdefault("performed_by_user_type", getattr(user, "user_type", None))
        if ip_address:
            normalized_details.setdefault("client_ip", ip_address)
        
        try:
            AuditLog.log(
                user=user,
                action=action,
                details=normalized_details,
                request=request,
                severity=derived_severity,
                response_status=response_status,
            )
        except Exception as e:
            # Fallback to console logging if database fails
            print(f"[AUDIT FALLBACK] {gateway_mode} - User: {user} - Action: {action} - Details: {details}")
            print(f"Audit logging error: {e}")

class IsOwnerOrAdmin(BasePermission):
    """
    Object-level permission to only allow owners or admins
    """
    def has_object_permission(self, request, view, obj):
        # Admin can do anything
        if getattr(request.user, 'user_type', None) in ADMIN_USER_TYPES or request.user.is_superuser:
            return True
        
        # Check if user owns this object
        if hasattr(obj, 'staff') and obj.staff and hasattr(obj.staff, 'user'):
            if obj.staff.user == request.user:
                return True
        
        if hasattr(obj, 'teacher') and obj.teacher and hasattr(obj.teacher, 'user'):
            if obj.teacher.user == request.user:
                return True
        
        return False


class PaymentTransactionPermission(BasePermission):
    """
    Special permissions for payment transactions
    """
    def has_permission(self, request, view):
        # Only admins can process payments
        if not (getattr(request.user, 'user_type', None) in ADMIN_USER_TYPES or request.user.is_superuser):
            return False
        
        # Additional check for bulk operations
        if 'bulk' in request.path.lower():
            # In dummy mode, skip approval
            if not getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False):
                return True
            if not getattr(settings, 'BULK_PAYMENT_APPROVAL_REQUIRED', True):
                return True
            
            # Require additional admin approval for bulk payments
            approval_token = request.headers.get('X-Bulk-Approval')
            if not approval_token:
                return False
            
            cached_user_id = cache.get(f"bulk_approval_{approval_token}")
            if not cached_user_id or int(cached_user_id) != request.user.id:
                return False
        
        return True
