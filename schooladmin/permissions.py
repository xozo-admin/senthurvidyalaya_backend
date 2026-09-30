from rest_framework.permissions import BasePermission

class IsAdmin(BasePermission):
    """
    Allows access only to Admin users.
    """
    def has_permission(self, request, view):
        # 1. User must be logged in
        # 2. User type must be 'admin' or 'super_admin'
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type in ('admin', 'super_admin')
        )


class IsSuperAdmin(BasePermission):
    """
    Allows access only to Super Admin users.
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == 'super_admin'
        )
