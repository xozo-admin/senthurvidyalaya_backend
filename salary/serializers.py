# salary/serializers.py

from rest_framework import serializers
from .models import SalaryStructure, TeacherSalaryStructure, StaffSalaryPayment, TeacherSalaryPayment
from teachers.models import Teacher
from staff.models import NonTeachingStaff

class SalaryStructureSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryStructure
        fields = ['id', 'staff_type', 'base_salary', 'late_penalty_percentage']


class TeacherSalaryStructureSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()

    class Meta:
        model = TeacherSalaryStructure
        fields = ['id', 'teacher_id', 'teacher_name', 'base_salary', 'late_penalty_percentage']

    def get_teacher_name(self, obj):
        try:
            teacher = Teacher.objects.get(teacher_id=obj.teacher_id)
            return teacher.name
        except Teacher.DoesNotExist:
            return "Unknown Teacher"


class StaffSalaryPaymentSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()
    staff_role = serializers.SerializerMethodField()
    staff_id_display = serializers.SerializerMethodField()
    total_working_days = serializers.SerializerMethodField()
    
    class Meta:
        model = StaffSalaryPayment
        fields = [
            'id', 'staff', 'staff_name', 'staff_role', 'staff_id_display', 'month', 'year',
            'base_salary', 'per_day_wage', 'days_worked', 'days_absent', 'days_late',
            'total_working_days',
            'absent_amount', 'late_amount', 'total_deduction', 'net_payable',
            'payment_date', 'transaction_id', 'bank_reference', 'transfer_bank_code', 'payment_status', 'remarks',
            'created_at'
        ]
        read_only_fields = ['created_at', 'payment_date']
    
    def get_staff_name(self, obj):
        return obj.staff.name
    
    def get_staff_role(self, obj):
        return obj.staff.role

    def get_staff_id_display(self, obj):
        return obj.staff.staff_id

    def get_total_working_days(self, obj):
        if obj.per_day_wage and obj.per_day_wage > 0:
            return int((obj.base_salary / obj.per_day_wage).to_integral_value())
        return 0


# NEW: Teacher Payment Serializer
class TeacherSalaryPaymentSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()
    teacher_id_display = serializers.SerializerMethodField()
    total_working_days = serializers.SerializerMethodField()
    
    class Meta:
        model = TeacherSalaryPayment
        fields = [
            'id', 'teacher', 'teacher_name', 'teacher_id_display', 'month', 'year',
            'base_salary', 'per_day_wage', 'days_worked', 'days_absent', 'days_late',
            'total_working_days',
            'absent_amount', 'late_amount', 'total_deduction', 'net_payable',
            'payment_date', 'transaction_id', 'bank_reference', 'transfer_bank_code', 'payment_status', 'remarks',
            'created_at'
        ]
        read_only_fields = ['created_at', 'payment_date']
    
    def get_teacher_name(self, obj):
        return obj.teacher.name
    
    def get_teacher_id_display(self, obj):
        return obj.teacher.teacher_id

    def get_total_working_days(self, obj):
        if obj.per_day_wage and obj.per_day_wage > 0:
            return int((obj.base_salary / obj.per_day_wage).to_integral_value())
        return 0
