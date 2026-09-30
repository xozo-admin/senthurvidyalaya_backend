from rest_framework import serializers
from .models import Teacher, TEACHER_PERMISSION_GROUPS, TEACHER_PERMISSION_LABELS, default_teacher_role_permissions
from academics.models import Standard, Section, ClassTeacher # <--- New Import
from school.models import AcademicYear
from school.tenant import get_active_academic_year, get_requested_school
from timetable.models import TimetableSlot

from rest_framework import serializers
from exams.models import ClassTest, ClassTestMarks, ExamTerm
from students.models import Student

# --- NEW IMPORTS FOR ATTENDANCE ---
from attendance.models import TeacherAttendance
from django.db.models import Q
from datetime import date, timedelta
import calendar
from holidays.models import Holiday  # (Make sure this matches your app name!)

class TeacherProfileSerializer(serializers.ModelSerializer):
    # INPUT FIELDS: Custom fields for Class/Section (Write Only)
    class_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    section = serializers.CharField(write_only=True, required=False, allow_blank=True)

    # --- NEW OUTPUT FIELDS ---
    today_attendance = serializers.SerializerMethodField()
    current_month_attendance_percentage = serializers.SerializerMethodField()
    current_year_attendance_percentage = serializers.SerializerMethodField()

    handled_subjects = serializers.SerializerMethodField()

    # 1. ADD THIS FIELD AT THE TOP:
    bus_number = serializers.SerializerMethodField()
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = Teacher
        fields = '__all__'
        read_only_fields = ['school']
        validators = []

    def _request_school(self):
        request = self.context.get("request")
        return get_requested_school(request) if request else None

    def validate_teacher_id(self, value):
        school = self._request_school() or getattr(self.instance, "school", None)
        if school:
            queryset = Teacher.objects.filter(school=school, teacher_id=value)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError("Teacher ID already exists in this school.")
        return value

        # --- ATTENDANCE HELPER METHOD ---
    def _calculate_percentage(self, teacher, start_date, end_date):
        records = TeacherAttendance.objects.filter(teacher=teacher, date__range=[start_date, end_date])
        att_map = {r.date: r for r in records}
        hols = Holiday.objects.filter(date__range=[start_date, end_date]).filter(
            Q(applicable_for='everyone') | Q(applicable_for='teachers')
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
        record = TeacherAttendance.objects.filter(teacher=obj, date=today).first()
        if record:
            return record.status
            
        if today.weekday() == 6:
            return "Sunday"
        if Holiday.objects.filter(date=today).filter(Q(applicable_for='everyone') | Q(applicable_for='teachers')).exists():
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

        ## attendance for teacher profile ends here

     # --- Fetch Handled Subjects for Active Year ---
    def get_handled_subjects(self, obj):
        try:
            active_year = get_active_academic_year(self.context.get("request"))
            if not active_year:
                raise AcademicYear.DoesNotExist
        except AcademicYear.DoesNotExist:
            return {}

        # 1. Fetch raw data from TimetableSlot for this specific teacher
        handled_items = TimetableSlot.objects.filter(
            teacher=obj,
            academic_year=active_year
        ).values(
            'section__standard__name', 
            'section__name', 
            'subject__name'
        ).distinct()

        # 2. Build the Nested Structure (Subject -> Class -> Sections)
        nested_data = {}

        for item in handled_items:
            class_name = item['section__standard__name']
            section_name = item['section__name']
            subject_name = item['subject__name']

            # Ensure the Subject exists
            if subject_name not in nested_data:
                nested_data[subject_name] = {}
            
            # Ensure the Class exists within that Subject
            if class_name not in nested_data[subject_name]:
                nested_data[subject_name][class_name] = []
            
            # Add the Section to the Class array
            if section_name not in nested_data[subject_name][class_name]:
                nested_data[subject_name][class_name].append(section_name)

        return nested_data

    # --- 1. READ (GET): Fetch Year-Safe Class ---
    def to_representation(self, instance):
        data = super().to_representation(instance)

        # Look for Active Year Assignment
        assignment = instance.class_teacher_of.filter(academic_year__is_current=True).first()

        if assignment:
            data['assigned_class'] = f"{assignment.section.standard.name} - {assignment.section.name}"
            data['section_name'] = assignment.section.name
            data['class_name'] = assignment.section.standard.name
        else:
            data['assigned_class'] = "Not Assigned"
            data['section_name'] = None
            data['class_name'] = None

        extra_data = data.pop('extra_details', {})
        if extra_data:
            data.update(extra_data)

        return data

    # --- 2. CREATE (POST): Create Teacher + ClassTeacher Record ---
    def create(self, validated_data):
        class_text = validated_data.pop('class_name', None)
        section_text = validated_data.pop('section', None)

        teacher = super().create(validated_data)

        if class_text and section_text:
            self._assign_class_teacher(teacher, class_text, section_text)
        
        return teacher

    # --- 3. UPDATE (PUT): Update ClassTeacher Record ---
    def update(self, instance, validated_data):
        class_text = validated_data.pop('class_name', None)
        section_text = validated_data.pop('section', None)

        # Update standard teacher fields
        teacher = super().update(instance, validated_data)

        # Handle Class Teacher assignment if fields are present
        if class_text is not None and section_text is not None:
             self._assign_class_teacher(teacher, class_text, section_text)

        return teacher

    # --- HELPER FUNCTION (DRY Logic) ---
    def _assign_class_teacher(self, teacher, class_name, section_name):
        """
        Manages the ClassTeacher record for the Active Year.
        """
        try:
            active_year = get_active_academic_year(self.context.get("request"))
            if not active_year:
                raise AcademicYear.DoesNotExist
        except AcademicYear.DoesNotExist:
            return # Cannot assign if no active year

        # CASE A: Clear Assignment (Empty Strings)
        if class_name == "" or section_name == "":
            ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).delete()
            return

        # CASE B: Assign New
        try:
            school = self._request_school() or teacher.school
            standard_query = Standard.objects.filter(name=class_name)
            if school:
                standard_query = standard_query.filter(school=school)
            standard_obj = standard_query.get()
            section_obj = Section.objects.get(standard=standard_obj, name=section_name)

            # 1. Remove any EXISTING assignment for this teacher in this year
            # (A teacher can't be class teacher of 10A and 10B at the same time)
            ClassTeacher.objects.filter(teacher=teacher, academic_year=active_year).delete()

            # 2. Remove any EXISTING assignment for this section in this year
            # (10A can't have two class teachers)
            ClassTeacher.objects.filter(section=section_obj, academic_year=active_year).delete()

            # 3. Create the new link
            ClassTeacher.objects.create(
                academic_year=active_year,
                school=school,
                teacher=teacher,
                section=section_obj
            )

        except (Standard.DoesNotExist, Section.DoesNotExist):
            pass # Invalid class/section name, ignore

        # 2. ADD THIS METHOD AT THE BOTTOM OF THE CLASS:
    def get_bus_number(self, obj):
        try:
            if hasattr(obj, 'transport_allocation') and obj.transport_allocation:
                return obj.transport_allocation.vehicle.bus_number
        except Exception:
            pass
        return "Not Assigned"


class TeacherRolePermissionSerializer(serializers.ModelSerializer):
    role_permissions = serializers.DictField(child=serializers.BooleanField(), required=False)
    permission_groups = serializers.SerializerMethodField()
    permission_labels = serializers.SerializerMethodField()
    enabled_count = serializers.SerializerMethodField()

    class Meta:
        model = Teacher
        fields = [
            'id',
            'teacher_id',
            'name',
            'department',
            'role_permissions',
            'permission_groups',
            'permission_labels',
            'enabled_count',
        ]
        read_only_fields = ['id', 'teacher_id', 'name', 'department', 'permission_groups', 'permission_labels', 'enabled_count']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['role_permissions'] = instance.normalized_role_permissions()
        return data

    def validate_role_permissions(self, value):
        allowed = set(default_teacher_role_permissions())
        normalized = default_teacher_role_permissions()
        normalized.update({key: bool(value[key]) for key in value if key in allowed})
        return normalized

    def get_permission_groups(self, obj):
        return TEACHER_PERMISSION_GROUPS

    def get_permission_labels(self, obj):
        return TEACHER_PERMISSION_LABELS

    def get_enabled_count(self, obj):
        return sum(1 for enabled in obj.normalized_role_permissions().values() if enabled)

class SimpleTeacherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ['teacher_id', 'name']



#class tests serializers

class ClassTestSerializer(serializers.ModelSerializer):
    term_name = serializers.CharField(write_only=True)
    class_name = serializers.CharField(source='section.standard.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    term_display = serializers.CharField(source='term.name', read_only=True)

    class Meta:
        model = ClassTest
        fields = ['id', 'test_name', 'max_marks', 'term_name', 'term_display', 
                 'class_name', 'section_name', 'subject_name', 'created_at']

class ClassTestMarkEntrySerializer(serializers.Serializer):
    student_id = serializers.CharField()
    marks = serializers.DecimalField(max_digits=5, decimal_places=2)

class ClassTestMarkViewSerializer(serializers.ModelSerializer):
    # We traverse: Mark -> Enrollment -> Student -> Name
    student_name = serializers.CharField(source='enrollment.student.student_name')
    student_id = serializers.CharField(source='enrollment.student.student_id')

    class Meta:
        model = ClassTestMarks
        fields = ['id', 'student_name', 'student_id', 'marks_obtained']
