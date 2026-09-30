from rest_framework import serializers
from django.contrib.auth import authenticate

from .models import Attendance, StaffAttendance
from rest_framework import serializers
from .models import StaffAttendance, TeacherAttendance, AttendanceConfig, QRAttendanceSession
# We don't import Student/Enrollment models here to avoid circular imports, 
# we rely on the related fields in the Attendance model.

# ==========================================
#  1. STUDENT ATTENDANCE SERIALIZERS
# ==========================================

class AttendanceSerializer(serializers.ModelSerializer):
    # Fetching details via the 'enrollment' relationship
    student_name = serializers.CharField(source='enrollment.student.student_name', read_only=True)
    custom_id = serializers.CharField(source='enrollment.student.student_id', read_only=True)
    standard = serializers.CharField(source='enrollment.standard.name', read_only=True)
    section = serializers.CharField(source='enrollment.section.name', read_only=True)

    class Meta:
        model = Attendance
        fields = ['date', 'student_name', 'custom_id', 'status', 'standard', 'section']

class StudentAttendanceHistorySerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Attendance
        fields = ['date', 'status', 'status_display']

class DailyAttendanceListSerializer(serializers.ModelSerializer):
    # Used for class summaries
    student_name = serializers.CharField(source='enrollment.student.student_name', read_only=True)
    student_id = serializers.CharField(source='enrollment.student.student_id', read_only=True)
    
    class Meta:
        model = Attendance
        fields = ['student_id', 'student_name', 'status']


# ==========================================
#  2. STAFF ATTENDANCE SERIALIZERS
# ==========================================

class StaffMarkAttendanceSerializer(serializers.Serializer):
    staff_id = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        # 1. Verify Credentials
        user = authenticate(username=data['staff_id'], password=data['password'])
        
        if not user:
            raise serializers.ValidationError("Invalid Staff ID or Password.")
        
        # 2. Check if they are actually Staff
        if not hasattr(user, 'staff_profile'):
            raise serializers.ValidationError("This user is not a Staff member.")

        return {
            'staff_profile': user.staff_profile
        }

class StaffAttendanceHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffAttendance
        fields = ['date', 'check_in_time', 'status']



class AttendanceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceConfig
        fields = '__all__'


class TeacherAttendanceHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherAttendance
        fields = ['date', 'check_in_time', 'status']

class DailyTeacherStatusSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    check_in_time = serializers.TimeField(allow_null=True)

class DailyStaffStatusSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    role = serializers.CharField()
    status = serializers.CharField()
    check_in_time = serializers.TimeField(allow_null=True)


class QRAttendanceSessionStartSerializer(serializers.Serializer):
    role_scope = serializers.ChoiceField(choices=['teacher', 'staff', 'both'], required=False, default='both')
    duration_minutes = serializers.IntegerField(required=False, default=1440, min_value=1, max_value=1440)
    rotation_seconds = serializers.IntegerField(required=False, default=30, min_value=10, max_value=120)


class QRAttendanceSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QRAttendanceSession
        fields = [
            'id',
            'role_scope',
            'starts_at',
            'ends_at',
            'rotation_seconds',
            'is_active',
            'closed_at',
            'created_at',
            'updated_at',
        ]


class QRAttendanceScanSerializer(serializers.Serializer):
    token = serializers.CharField()
