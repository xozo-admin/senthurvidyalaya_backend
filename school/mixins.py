from rest_framework.exceptions import ValidationError
from .models import AcademicYear
from .tenant import get_active_academic_year, scope_queryset_for_user

# ==========================================
# 1. FOR DIRECT MODELS (Timetable, Holidays, Announcements)
# ==========================================
class AcademicYearContextMixin:
    """
    Enforces that all CRUD operations are scoped to the Active Academic Year.
    Assumes the model has a direct 'academic_year' field.
    """
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Admin Override
        year_id = self.request.query_params.get('year_id')
        if year_id:
            return queryset.filter(academic_year_id=year_id)
        
        # Standard Mode
        try:
            active_year = get_active_academic_year(self.request)
            if not active_year:
                raise AcademicYear.DoesNotExist
            return queryset.filter(academic_year=active_year)
        except AcademicYear.DoesNotExist:
            return queryset.none()

    def perform_create(self, serializer):
        try:
            active_year = get_active_academic_year(self.request)
            if not active_year:
                raise AcademicYear.DoesNotExist
            serializer.save(academic_year=active_year)
        except AcademicYear.DoesNotExist:
            raise ValidationError({"error": "No Active Academic Year set."})


# ==========================================
# 2. FOR ENROLLMENT MODELS (Attendance, Marks, Fees)
# ==========================================
class EnrollmentYearAccessMixin:
    """
    Helper for models that link to Academic Year via 'enrollment'.
    Provides logic to determine which years a user is allowed to see.
    """

    def get_allowed_academic_years(self, user):
        """
        Returns a QuerySet of AcademicYears this user is allowed to access.
        - Admin: All Years.
        - Teacher/Student: Only the Active Year.
        """
        if getattr(user, 'user_type', None) in ('admin', 'super_admin'):
            return scope_queryset_for_user(AcademicYear.objects.all(), self.request)
        active_year = get_active_academic_year(self.request)
        return AcademicYear.objects.filter(pk=active_year.pk) if active_year else AcademicYear.objects.none()

    def get_active_year(self):
        """Helper to safely get the current active year."""
        try:
            return get_active_academic_year(self.request)
        except Exception:
            return None
