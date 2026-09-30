from rest_framework.permissions import BasePermission


class IsAdminUserType(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.user.user_type in ('admin', 'super_admin'):
            return True

        if request.user.user_type == 'staff' and hasattr(request.user, 'staff_profile'):
            return getattr(request.user.staff_profile, 'role', None) == 'admin_staff'

        return False


class IsTeacherUserType(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'teacher')


class IsStaffUserType(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'staff')


class IsStudentUserType(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'student')


class IsTeacherOrStaffUserType(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type in {'teacher', 'staff'}
        )
