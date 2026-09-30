from rest_framework import serializers
from .models import (
    NonTeachingStaff,
    StaffRolePermission,
    STAFF_PERMISSION_GROUPS,
    STAFF_PERMISSION_LABELS,
    default_staff_role_permissions,
)
# --- NEW IMPORTS FOR ATTENDANCE ---
from attendance.models import StaffAttendance
from django.db.models import Q
from datetime import date, timedelta
import calendar
from holidays.models import Holiday
from school.tenant import get_requested_school


# --- 1. For Viewing Profile ---
class StaffProfileSerializer(serializers.ModelSerializer):

    # --- NEW OUTPUT FIELDS ---
    today_attendance = serializers.SerializerMethodField()
    current_month_attendance_percentage = serializers.SerializerMethodField()
    current_year_attendance_percentage = serializers.SerializerMethodField()

    # 1. ADD THIS FIELD AT THE TOP:
    bus_number = serializers.SerializerMethodField()
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = NonTeachingStaff
        fields = '__all__'
        read_only_fields = ['school']
        validators = []

    def _request_school(self):
        request = self.context.get("request")
        return get_requested_school(request) if request else None

    def validate_staff_id(self, value):
        school = self._request_school() or getattr(self.instance, "school", None)
        if school:
            queryset = NonTeachingStaff.objects.filter(school=school, staff_id=value)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError("Staff ID already exists in this school.")
        return value

        # --- ATTENDANCE HELPER METHOD ---
    def _calculate_percentage(self, staff, start_date, end_date):
        records = StaffAttendance.objects.filter(staff=staff, date__range=[start_date, end_date])
        att_map = {r.date: r for r in records}
        
        # Matches your view: checking for 'everyone' or 'staff'
        hols = Holiday.objects.filter(date__range=[start_date, end_date]).filter(
            Q(applicable_for='everyone') | Q(applicable_for='staff') 
        )
        hol_list = list(hols.values_list('date', flat=True))

        s_cnt, h_cnt, p_cnt, l_cnt = 0, 0, 0, 0
        curr = start_date
        
        while curr <= end_date:
            rec = att_map.get(curr)
            if rec:
                if rec.status == 'Present': p_cnt += 1
                else: l_cnt += 1  # Treats 'Late' as present for the percentage math
            elif curr.weekday() == 6: 
                s_cnt += 1
            elif curr in hol_list: 
                h_cnt += 1
            
            curr += timedelta(days=1)

        w_days = (end_date - start_date).days + 1 - s_cnt - h_cnt
        
        if w_days > 0:
            return f"{round(((p_cnt + l_cnt) / w_days) * 100, 1)}%"
        return "Not Marked"

    # --- 1. Get Today's Status ---
    def get_today_attendance(self, obj):
        today = date.today()
        record = StaffAttendance.objects.filter(staff=obj, date=today).first()
        if record:
            return record.status
            
        if today.weekday() == 6:
            return "Sunday"
        if Holiday.objects.filter(date=today).filter(Q(applicable_for='everyone') | Q(applicable_for='staff')).exists():
            return "Holiday"
            
        return "Not Marked"

    # --- 2. Get Current Month Percentage ---
    def get_current_month_attendance_percentage(self, obj):
        today = date.today()
        start_date = date(today.year, today.month, 1)
        end_date = today 
        return self._calculate_percentage(obj, start_date, end_date)

    # --- 3. Get Current Year Percentage ---
    def get_current_year_attendance_percentage(self, obj):
        today = date.today()
        start_date = date(today.year, 1, 1)
        end_date = today
        return self._calculate_percentage(obj, start_date, end_date)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        extra_data = data.pop('extra_details', {})
        if extra_data:
            data.update(extra_data)
        return data

        # 2. ADD THIS METHOD AT THE BOTTOM OF THE CLASS:
    def get_bus_number(self, obj):
        # Check 1: Is this staff member the DRIVER of a bus?
        try:
            if hasattr(obj, 'assigned_bus') and obj.assigned_bus:
                return f"{obj.assigned_bus.bus_number} (Driver)"
        except Exception:
            pass
            
        # Check 2: Is this staff member a PASSENGER on a bus?
        try:
            if hasattr(obj, 'transport_allocation') and obj.transport_allocation:
                return obj.transport_allocation.vehicle.bus_number
        except Exception:
            pass
            
        # If neither, return Not Assigned
        return "Not Assigned"

# --- 2. For Registration (SIMPLIFIED) ---
class StaffRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = NonTeachingStaff
        # No 'username' or 'password' needed here. 
        # The Signal will generate them automatically from 'staff_id' and 'phone'.
        fields = [
            'school',
            'staff_id',
            'name',
            'phone',
            'joining_date',
            'profile_image',
            'role',
            'email',
            'address',
            'extra_details',
            'bank_account_number',
            'ifsc_code',
            'account_holder_name',
            'bank_name',
            'upi_id',
        ]
        read_only_fields = ['school']


class StaffRolePermissionSerializer(serializers.ModelSerializer):
    role_permissions = serializers.DictField(child=serializers.BooleanField(), required=False)
    permission_groups = serializers.SerializerMethodField()
    permission_labels = serializers.SerializerMethodField()
    enabled_count = serializers.SerializerMethodField()
    role_label = serializers.SerializerMethodField()
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = StaffRolePermission
        fields = [
            'id',
            'school',
            'school_name',
            'role',
            'role_label',
            'role_permissions',
            'permission_groups',
            'permission_labels',
            'enabled_count',
        ]
        read_only_fields = [
            'id',
            'school',
            'school_name',
            'role_label',
            'permission_groups',
            'permission_labels',
            'enabled_count',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['role_permissions'] = instance.normalized_role_permissions()
        return data

    def validate_role_permissions(self, value):
        allowed = set(default_staff_role_permissions())
        normalized = default_staff_role_permissions()
        normalized.update({key: bool(value[key]) for key in value if key in allowed})
        return normalized

    def get_permission_groups(self, obj):
        return STAFF_PERMISSION_GROUPS

    def get_permission_labels(self, obj):
        return STAFF_PERMISSION_LABELS

    def get_enabled_count(self, obj):
        return sum(1 for enabled in obj.normalized_role_permissions().values() if enabled)

    def get_role_label(self, obj):
        return obj.get_role_display()
