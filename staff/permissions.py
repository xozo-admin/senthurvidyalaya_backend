from rest_framework.permissions import BasePermission

# 1. Custom Admin Permission (Matches your Teacher app style)
class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        # Checks if the user is logged in AND is an 'admin'
        return bool(request.user and request.user.is_authenticated and request.user.user_type in ('admin', 'super_admin'))

# 2. Custom Staff Permission
class IsStaff(BasePermission):
    def has_permission(self, request, view):
        # Checks if user_type is 'staff'
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'staff')
    

class IsOwnerAdminOrFinanceAdminStaff(BasePermission):
    """
    Allows access to:
    1) Main school owner/admin login (user_type='admin')
    2) Staff login with role in {'admin_staff', 'finance_staff'}
    """

    ALLOWED_STAFF_ROLES = {'admin_staff', 'finance_staff'}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        # Main school owner/admin login
        if user.user_type in ('admin', 'super_admin'):
            return True

        # Staff login with allowed staff role
        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            staff_role = getattr(user.staff_profile, 'role', None)
            return staff_role in self.ALLOWED_STAFF_ROLES

        return False


class IsOwnerAdminOrAdminStaff(BasePermission):
    """
    Allows access to:
    1) Main school owner/admin login (user_type='admin')
    2) Staff login with role 'admin_staff'
    """

    ALLOWED_STAFF_ROLES = {'admin_staff'}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.user_type in ('admin', 'super_admin'):
            return True

        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            staff_role = getattr(user.staff_profile, 'role', None)
            return staff_role in self.ALLOWED_STAFF_ROLES

        return False


class IsOwnerAdminOrAdminStaffOrHostelWarden(BasePermission):
    """
    Allows access to:
    1) Main school owner/admin login (user_type='admin')
    2) Staff login with role in {'admin_staff', 'hostel_warden'}
    """

    ALLOWED_STAFF_ROLES = {'admin_staff', 'hostel_warden'}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.user_type in ('admin', 'super_admin'):
            return True

        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            staff_role = getattr(user.staff_profile, 'role', None)
            return staff_role in self.ALLOWED_STAFF_ROLES

        return False


class IsOwnerAdminOrFinanceAdminStaffOrHostelWarden(BasePermission):
    """
    Allows access to:
    1) Main school owner/admin login (user_type='admin')
    2) Staff login with role in {'admin_staff', 'finance_staff', 'hostel_warden'}
    """

    ALLOWED_STAFF_ROLES = {'admin_staff', 'finance_staff', 'hostel_warden'}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.user_type in ('admin', 'super_admin'):
            return True

        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            staff_role = getattr(user.staff_profile, 'role', None)
            return staff_role in self.ALLOWED_STAFF_ROLES

        return False
