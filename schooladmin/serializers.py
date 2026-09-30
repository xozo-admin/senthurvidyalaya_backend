from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import AdminProfile 
from school.models import AcademicYear  # <--- NEW IMPORT

# Get the custom User model
User = get_user_model()

class AdminProfileSerializer(serializers.ModelSerializer):
    # --- NEW FIELDS: Fetch School & Year Details ---
    school_name = serializers.SerializerMethodField()
    school_id = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    current_academic_year = serializers.SerializerMethodField() # <--- Very Useful Addition
    institution_id = serializers.SerializerMethodField()
    institution_name = serializers.SerializerMethodField()
    can_access_all_schools = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'username', 'first_name', 'last_name', 'full_name', 
            'email', 'phone', 'user_type', 
            'school_name', 'school_id',
            'institution_id', 'institution_name', 'can_access_all_schools',
            'current_academic_year' # <--- Added here
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()

    def get_school_name(self, obj):
        try:
            return obj.adminprofile.school.name
        except (AdminProfile.DoesNotExist, AttributeError):
            return "No School Assigned"

    def get_school_id(self, obj):
        try:
            return obj.adminprofile.school.id
        except (AdminProfile.DoesNotExist, AttributeError):
            return None

    # --- NEW METHOD: Fetch Active Year ---
    def get_current_academic_year(self, obj):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            return active_year.name  # e.g., "2025-2026"
        except AcademicYear.DoesNotExist:
            return "Not Set"

    def get_institution_id(self, obj):
        try:
            return obj.adminprofile.school.institution_id
        except (AdminProfile.DoesNotExist, AttributeError):
            return None

    def get_institution_name(self, obj):
        try:
            institution = obj.adminprofile.school.institution
            return institution.name if institution else None
        except (AdminProfile.DoesNotExist, AttributeError):
            return "Institution Overview" if obj.user_type == 'super_admin' else None

    def get_can_access_all_schools(self, obj):
        return obj.user_type == 'super_admin'


class AdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    school_id = serializers.SerializerMethodField()
    school_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'full_name',
            'email', 'phone', 'user_type', 'is_active', 'school_id', 'school_name'
        ]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.first_name or obj.username

    def get_school_id(self, obj):
        try:
            return obj.adminprofile.school_id
        except (AdminProfile.DoesNotExist, AttributeError):
            return None

    def get_school_name(self, obj):
        try:
            return obj.adminprofile.school.name
        except (AdminProfile.DoesNotExist, AttributeError):
            return "No School Assigned"
