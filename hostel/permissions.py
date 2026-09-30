from rest_framework.permissions import BasePermission


ADMIN_USER_TYPES = {'admin', 'super_admin'}
HOSTEL_ALLOWED_STAFF_ROLES = {'admin_staff', 'hostel_warden'}
HOSTEL_ADMIN_STAFF_ROLES = {'admin_staff'}


class IsAdminUserType(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.user_type in ADMIN_USER_TYPES:
            return True

        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            return getattr(user.staff_profile, 'role', None) in HOSTEL_ADMIN_STAFF_ROLES

        return False


class IsHostelStaffUserType(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.user_type == 'staff' and hasattr(user, 'staff_profile')):
            return False
        return getattr(user.staff_profile, 'role', None) in HOSTEL_ALLOWED_STAFF_ROLES


class IsAdminOrHostelStaff(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.user_type in ADMIN_USER_TYPES:
            return True
        if user.user_type == 'staff' and hasattr(user, 'staff_profile'):
            return getattr(user.staff_profile, 'role', None) in HOSTEL_ALLOWED_STAFF_ROLES
        return False


class IsStudentUserType(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'student')
