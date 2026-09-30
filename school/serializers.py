from rest_framework import serializers

from .models import AcademicYear, Institution, School


class InstitutionSerializer(serializers.ModelSerializer):
    school_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Institution
        fields = [
            'id', 'name', 'address', 'contact_phone', 'contact_email',
            'logo', 'is_active', 'created_at', 'school_count'
        ]


class SchoolSerializer(serializers.ModelSerializer):
    institution_name = serializers.CharField(source='institution.name', read_only=True)
    student_count = serializers.IntegerField(read_only=True)
    teacher_count = serializers.IntegerField(read_only=True)
    staff_count = serializers.IntegerField(read_only=True)
    admin_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = School
        fields = [
            'id', 'institution', 'institution_name', 'name', 'code',
            'address', 'contact_phone', 'contact_email', 'logo', 'is_active',
            'total_students', 'total_staffs', 'total_teachers',
            'total_non_teaching', 'student_count', 'teacher_count',
            'staff_count', 'admin_count'
        ]


class AcademicYearSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = AcademicYear
        fields = ['id', 'school', 'school_name', 'name', 'start_date', 'end_date', 'is_current']
        read_only_fields = ['school']

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError("End date must be after start date.")
        return data
