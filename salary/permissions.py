# salary/permissions.py (New file)
from rest_framework.permissions import BasePermission, SAFE_METHODS
from django.core.cache import cache

ADMIN_USER_TYPES = {'admin', 'super_admin'}

class IsOwnerOrAdmin(BasePermission):
    """
    Object-level permission to only allow owners or admins
    """
    def has_object_permission(self, request, view, obj):
        # Admin can do anything
        if getattr(request.user, 'user_type', None) in ADMIN_USER_TYPES or request.user.is_superuser:
            return True
        
        # Check if user owns this object
        if hasattr(obj, 'staff') and obj.staff.user == request.user:
            return True
        if hasattr(obj, 'teacher') and obj.teacher.user == request.user:
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
        if 'bulk' in request.path:
            # Require additional admin approval for bulk payments
            approval_token = request.headers.get('X-Bulk-Approval')
            if not approval_token or not cache.get(f"bulk_approval_{approval_token}"):
                return False
        
        return True
