from rest_framework.permissions import BasePermission

class IsStudent(BasePermission):
    """
    Allows access only if the logged-in user is a Student.
    """
    def has_permission(self, request, view):
        # Check if user is logged in AND user_type is 'student'
        return bool(request.user and request.user.is_authenticated and request.user.user_type == 'student')