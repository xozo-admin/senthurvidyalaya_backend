from rest_framework import serializers
from .models import (
    Student,
    Enrollment,
    StudentRolePermission,
    STUDENT_PERMISSION_GROUPS,
    STUDENT_PERMISSION_LABELS,
    default_student_role_permissions,
)
from academics.models import Standard, Section
from school.models import AcademicYear # <--- NEW IMPORT
from school.tenant import get_active_academic_year, get_requested_school
from django.utils import timezone
from attendance.models import Attendance  # <--- IMPORT THIS

class StudentProfileSerializer(serializers.ModelSerializer):
    # INPUT: Text fields for writing (e.g. "10", "A")
    class_name = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    section = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    student_email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)

    # --- NEW OUTPUT FIELDS: For Attendance ---
    today_attendance = serializers.SerializerMethodField()
    current_month_attendance_percentage = serializers.SerializerMethodField()
    academic_year_attendance_percentage = serializers.SerializerMethodField()

    # ----------------------------------------------------
    # YOU MUST ADD THIS LINE RIGHT HERE:
    bus_number = serializers.SerializerMethodField() 
    school_name = serializers.CharField(source='school.name', read_only=True)
    # ----------------------------------------------------

    class Meta:
        model = Student
        fields = [
            'student_id', 
            'school',
            'school_name',
            'student_name', 
            'profile_image',
            'class_name', 
            'section', 
            'student_email', 
            'father_phone', 
            'mother_phone', 
            'father_name', 
            'mother_name', 
            'address', 
            'date_of_birth', 
            'date_of_admission',
            'gender', 
            'accommodation',
            'bus_number',
            'extra_details',
            # Add the new fields here:
            'today_attendance',
            'current_month_attendance_percentage',
            'academic_year_attendance_percentage'
        ]
        read_only_fields = ['school']
        validators = []

    def _request_school(self):
        request = self.context.get("request")
        return get_requested_school(request) if request else None

    def validate_student_id(self, value):
        school = self._request_school() or getattr(self.instance, "school", None)
        if school:
            queryset = Student.objects.filter(school=school, student_id=value)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError("Student ID already exists in this school.")
        return value

    # Helper method to get the active enrollment for the student
    def _get_active_enrollment(self, student_obj):
        try:
            active_year = get_active_academic_year(self.context.get("request"))
            if not active_year:
                raise AcademicYear.DoesNotExist
            return Enrollment.objects.filter(
                student=student_obj, 
                academic_year=active_year, 
                is_active=True
            ).first()
        except AcademicYear.DoesNotExist:
            return None

    # --- 1. Get Today's Status ---
    def get_today_attendance(self, obj):
        enrollment = self._get_active_enrollment(obj)
        if not enrollment:
            return "Not Marked"

        today = timezone.now().date()
        record = Attendance.objects.filter(enrollment=enrollment, date=today).first()
        return record.status if record else "Not Marked"

    # --- 2. Get Current Month Percentage ---
    def get_current_month_attendance_percentage(self, obj):
        enrollment = self._get_active_enrollment(obj)
        if not enrollment:
            return "Not Marked"

        today = timezone.now().date()
        records = Attendance.objects.filter(
            enrollment=enrollment, 
            date__month=today.month, 
            date__year=today.year
        )
        
        total_days = records.count()
        if total_days == 0:
            return "Not Marked"

        # Count how many days the student was 'Present'
        present_count = records.filter(status__iexact='present').count()
        percentage = (present_count / total_days) * 100
        return f"{round(percentage, 1)}%"

    # --- 3. Get Whole Academic Year Percentage ---
    def get_academic_year_attendance_percentage(self, obj):
        enrollment = self._get_active_enrollment(obj)
        if not enrollment:
            return "Not Marked"

        # Get all attendance records for this student's current enrollment
        records = Attendance.objects.filter(enrollment=enrollment)
        
        total_days = records.count()
        if total_days == 0:
            return "Not Marked"

        present_count = records.filter(status__iexact='present').count()
        percentage = (present_count / total_days) * 100
        return f"{round(percentage, 1)}%"

        # 3. PASTE IT EXACTLY HERE (Inside the class, at the bottom)
    def get_bus_number(self, obj):
        try:
            # If they have a bus, it returns the bus number
            if hasattr(obj, 'transport_allocation') and obj.transport_allocation:
                return obj.transport_allocation.vehicle.bus_number
        except Exception:
            pass
        # If they DON'T have a bus, it safely falls down to this line:
        return "Not Assigned"

    # --- 1. OUTPUT (GET) ---
    def to_representation(self, instance):
        data = super().to_representation(instance)

        target_year = self.context.get("academic_year")
        if target_year:
            year_enrollment = None
            prefetched = getattr(instance, "enrollments_for_year", None)
            if prefetched:
                year_enrollment = prefetched[0]
            else:
                year_enrollment = instance.enrollments.filter(academic_year=target_year).select_related(
                    "standard", "section"
                ).first()

            if year_enrollment and year_enrollment.standard:
                data['class_name'] = year_enrollment.standard.name
            else:
                data['class_name'] = "Not Assigned"

            if year_enrollment and year_enrollment.section:
                data['section'] = year_enrollment.section.name
            else:
                data['section'] = "N/A"
        else:
            # We rely on the Student model's current fields when no target academic year is requested.
            if instance.standard:
                data['class_name'] = instance.standard.name
            elif instance.section:
                data['class_name'] = instance.section.standard.name
            else:
                data['class_name'] = "Not Assigned"

            if instance.section:
                data['section'] = instance.section.name
            else:
                data['section'] = "N/A"

        # Flatten extra_details
        extra_data = data.pop('extra_details', {})
        if extra_data:
            data.update(extra_data)

        return data
        
    # --- 2. CREATE (POST) - UPDATED FOR ENROLLMENT ---
    def create(self, validated_data):
        class_text = validated_data.pop('class_name', None)
        section_text = validated_data.pop('section', None)

        # A. Resolve Class/Section Objects
        standard_obj = None
        section_obj = None

        if class_text:
            try:
                standard_query = Standard.objects.filter(name=class_text)
                school = self._request_school()
                if school:
                    standard_query = standard_query.filter(school=school)
                standard_obj = standard_query.get()
                validated_data['standard'] = standard_obj 
            except Standard.DoesNotExist:
                raise serializers.ValidationError({"class_name": f"Class '{class_text}' does not exist."})

        if section_text and standard_obj:
            try:
                section_obj = Section.objects.get(standard=standard_obj, name=section_text)
                validated_data['section'] = section_obj
            except Section.DoesNotExist:
                raise serializers.ValidationError({"section": f"Section '{section_text}' does not exist in Class '{class_text}'."})

        # B. Create Student
        student = super().create(validated_data)

        # C. AUTO-ENROLL (The Fix)
        # If class info is present, strictly create an Enrollment for the Active Year
        if standard_obj:
            try:
                active_year = get_active_academic_year(self.context.get("request"))
                if not active_year:
                    raise AcademicYear.DoesNotExist
                Enrollment.objects.create(
                    school=student.school,
                    student=student,
                    academic_year=active_year,
                    standard=standard_obj,
                    section=section_obj
                )
            except AcademicYear.DoesNotExist:
                # If no active year, we save the student but can't enroll them.
                # This prevents a crash, but in production, you should ensure a year exists.
                pass 

        return student

    # --- 3. UPDATE (PUT) - UPDATED FOR ENROLLMENT ---
    def update(self, instance, validated_data):
        class_text = validated_data.pop('class_name', None)
        section_text = validated_data.pop('section', None)
        class_was_sent = 'class_name' in getattr(self, 'initial_data', {})
        section_was_sent = 'section' in getattr(self, 'initial_data', {})

        standard_obj = instance.standard 
        section_obj = instance.section
        has_class_change = False

        # A. Resolve Changes
        if class_was_sent and not class_text:
            standard_obj = None
            section_obj = None
            instance.standard = None
            instance.section = None
            has_class_change = True
        elif class_text:
            try:
                standard_query = Standard.objects.filter(name=class_text)
                school = self._request_school()
                if school:
                    standard_query = standard_query.filter(school=school)
                standard_obj = standard_query.get()
                instance.standard = standard_obj
                if not section_was_sent:
                    section_obj = None
                    instance.section = None
                has_class_change = True
            except Standard.DoesNotExist:
                raise serializers.ValidationError({"class_name": f"Class '{class_text}' does not exist."})

        if section_was_sent and not section_text:
            section_obj = None
            instance.section = None
            has_class_change = True
        elif section_text and standard_obj:
            try:
                section_obj = Section.objects.get(standard=standard_obj, name=section_text)
                instance.section = section_obj
                has_class_change = True
            except Section.DoesNotExist:
                raise serializers.ValidationError({"section": f"Section '{section_text}' does not exist in Class '{class_text}'."})

        # B. Update Student Profile
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # C. SYNC ENROLLMENT (The Fix)
        if has_class_change:
            try:
                active_year = get_active_academic_year(self.context.get("request"))
                if not active_year:
                    raise AcademicYear.DoesNotExist
                
                if standard_obj:
                    # Update existing enrollment or create new one for this year
                    Enrollment.objects.update_or_create(
                        student=instance,
                        academic_year=active_year,
                        defaults={
                            'school': instance.school,
                            'standard': standard_obj,
                            'section': section_obj
                        }
                    )
                else:
                    Enrollment.objects.filter(student=instance, academic_year=active_year).delete()
            except AcademicYear.DoesNotExist:
                pass

        return instance


# --- KEEPING EXISTING SERIALIZERS ---
class SimpleStudentSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = Student
        fields = ['student_id', 'student_name', 'profile_image', 'school_name']

class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = '__all__'

class EnrollmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.student_name', read_only=True)
    student_code = serializers.CharField(source='student.student_id', read_only=True)
    student_gender = serializers.CharField(source='student.gender', read_only=True)
    student_accommodation = serializers.CharField(source='student.accommodation', read_only=True)
    standard_name = serializers.CharField(source='standard.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    academic_year_name = serializers.CharField(source='academic_year.name', read_only=True)

    # ---> ADD THESE TWO LINES HERE <---
    student_profile_image = serializers.ImageField(source='student.profile_image', read_only=True)
    bus_number = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = [
            'id', 'student', 'student_name', 'student_code', 'student_gender', 'student_accommodation',
            'standard', 'standard_name', 
            'section', 'section_name', 
            'academic_year', 'academic_year_name',
            'is_active', 'promoted', 'created_at','student_profile_image',
            'bus_number',
        ]
        read_only_fields = ['academic_year', 'created_at']

        # --- ADD THIS METHOD FOR ENROLLMENT ---
    def get_bus_number(self, obj):
        try:
            # Notice it uses obj.student here, because the main object is Enrollment!
            if hasattr(obj.student, 'transport_allocation') and obj.student.transport_allocation:
                return obj.student.transport_allocation.vehicle.bus_number
        except Exception:
            pass
        return "Not Assigned"


class StudentRolePermissionSerializer(serializers.ModelSerializer):
    role_permissions = serializers.DictField(child=serializers.BooleanField(), required=False)
    permission_groups = serializers.SerializerMethodField()
    permission_labels = serializers.SerializerMethodField()
    enabled_count = serializers.SerializerMethodField()
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = StudentRolePermission
        fields = [
            'id',
            'school',
            'school_name',
            'role_permissions',
            'permission_groups',
            'permission_labels',
            'enabled_count',
        ]
        read_only_fields = [
            'id',
            'school',
            'school_name',
            'permission_groups',
            'permission_labels',
            'enabled_count',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['role_permissions'] = instance.normalized_role_permissions()
        return data

    def validate_role_permissions(self, value):
        allowed = set(default_student_role_permissions())
        normalized = default_student_role_permissions()
        normalized.update({key: bool(value[key]) for key in value if key in allowed})
        return normalized

    def get_permission_groups(self, obj):
        return STUDENT_PERMISSION_GROUPS

    def get_permission_labels(self, obj):
        return STUDENT_PERMISSION_LABELS

    def get_enabled_count(self, obj):
        return sum(1 for enabled in obj.normalized_role_permissions().values() if enabled)
