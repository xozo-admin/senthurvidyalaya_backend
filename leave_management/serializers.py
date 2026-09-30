from rest_framework import serializers
from .models import LeaveRequest

class LeaveRequestSerializer(serializers.ModelSerializer):
    # Custom fields for easier Frontend display
    requester_name = serializers.SerializerMethodField()
    requester_id = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    
    # This fetches the name (e.g., "2025-2026") from the linked AcademicYear model
    academic_year_name = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user_type', 'role', 
            'requester_name', 'requester_id', 
            'academic_year', 'academic_year_name',
            'start_date', 'end_date', 'reason', 'proof_file', 
            'status', 'admin_comment', 'approved_by_name', 'created_at'
        ]
        read_only_fields = ['status', 'admin_comment', 'user_type', 'academic_year', 'approved_by_name']

    def get_requester_name(self, obj):
        if obj.student: return obj.student.student_name  # Ensure this matches your Student model field (name vs student_name)
        if obj.staff: return obj.staff.name
        if obj.teacher: return obj.teacher.name
        return "Unknown"

    def get_requester_id(self, obj):
        if obj.student: return obj.student.student_id
        if obj.staff: return obj.staff.staff_id
        if obj.teacher: return obj.teacher.teacher_id
        return "Unknown"

    def get_role(self, obj):
        if obj.user_type == 'staff' and obj.staff:
            return obj.staff.role
        return ''
