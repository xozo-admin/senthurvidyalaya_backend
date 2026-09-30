# timetable/serializers.py

from typing import Dict

from rest_framework import serializers
from .models import TimetableSlot, Substitution, ClassBreak
from academics.models import Standard, Section
from teachers.models import Teacher, TeacherAllocation
from subjects.models import Subject
from school.models import AcademicYear # <--- NEW IMPORT
from django.db.models import Q 
from .scope import (
    get_scoped_active_year,
    get_scoped_section,
    get_scoped_standard,
    scoped_sections,
    scoped_teachers,
)

# 1. Helper Serializer for Input
class PeriodInputSerializer(serializers.Serializer):
    period_no = serializers.IntegerField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    subject = serializers.CharField()
    teacher_id = serializers.CharField()

# 2. Main Timetable Creation Serializer
class TimetableCreateSerializer(serializers.Serializer):
    class_name = serializers.CharField()
    section = serializers.CharField()
    day = serializers.ChoiceField(choices=TimetableSlot.DAYS_OF_WEEK)
    timetable = PeriodInputSerializer(many=True)

    def validate(self, data):
        """
        Perform complex checks: Class existence, Teacher Allocation (Year-Safe), and Time Conflicts.
        """
        class_name = data['class_name']
        section_name = data['section']
        day = data['day']
        periods_data = data['timetable']

        # 1. Fetch Active Year (Crucial for Validation)
        request = self.context.get("request")
        active_year = get_scoped_active_year(request)
        if not active_year:
            raise serializers.ValidationError("No Active Academic Year found.")

        # 2. Basic Class/Section Validation
        try:
            standard_obj = get_scoped_standard(request, class_name)
            section_obj = get_scoped_section(request, standard_obj, section_name)
        except (Standard.DoesNotExist, Section.DoesNotExist):
            raise serializers.ValidationError(f"Invalid Class {class_name} or Section {section_name}.")

        for period in periods_data:
            t_id = period['teacher_id']
            sub_name = period['subject']
            p_start = period['start_time']
            p_end = period['end_time']

            # ======================================================
            # NEW VALIDATION: Check against Breaks (Lunch, Interval)
            # ======================================================
            # Find any break for this class that overlaps with this period
            conflict_break = ClassBreak.objects.filter(
                standard=standard_obj,
                academic_year=active_year,
                start_time__lt=p_end,  # Break starts before Class ends
                end_time__gt=p_start   # Break ends after Class starts
            ).first()

            if conflict_break:
                raise serializers.ValidationError(
                    f"Cannot schedule class at {p_start}. "
                    f"'{conflict_break.name}' starts at {conflict_break.start_time} and ends at {conflict_break.end_time}."
                )
            # ======================================================

            # --- A. Find Teacher & Subject ---
            try:
                teacher_obj = scoped_teachers(request).get(teacher_id=t_id)
            except Teacher.DoesNotExist:
                raise serializers.ValidationError(f"Teacher ID {t_id} not found.")

            try:
                subject_obj = Subject.objects.get(standard=standard_obj, name__iexact=sub_name)
            except Subject.DoesNotExist:
                raise serializers.ValidationError(f"Subject '{sub_name}' does not exist for Class {class_name}.")

            # --- CHECK 1: AUTHORIZATION (Year-Safe) ---
            # Logic: Check if TeacherAllocation exists for this Teacher + Subject + Standard + Active Year
            is_allocated = TeacherAllocation.objects.filter(
                teacher=teacher_obj, 
                subject=subject_obj,
                standard=standard_obj, # <--- Added Standard check
                academic_year=active_year # <--- Added Year check
            ).exists()
            
            if not is_allocated:
                # Custom Error Message (Preserved exactly as requested)
                raise serializers.ValidationError(
                    f"{teacher_obj.teacher_id} {teacher_obj.name} is not assigned to handle Class {class_name} - {sub_name}."
                )

            # --- CHECK 2: CONFLICT (Is he busy?) ---
            # Logic: Look for overlapping slots for this teacher, on this day, IN THE CURRENT YEAR.
            conflicts = TimetableSlot.objects.filter(
                teacher=teacher_obj,
                day=day,
                academic_year=active_year # <--- Year Safety
            ).exclude(section=section_obj, period_no=period['period_no']) 

            # Time Overlap Formula
            for slot in conflicts:
                if p_start < slot.end_time and p_end > slot.start_time:
                    raise serializers.ValidationError(
                        f"{teacher_obj.teacher_id} {teacher_obj.name} is already assigned for "
                        f"{slot.section.standard.name}-{slot.section.name} in this time (Period {slot.period_no})."
                    )

        # Pass objects to create method
        data['standard_obj'] = standard_obj
        data['section_obj'] = section_obj
        # We don't pass active_year here because the View passes it directly to save(), 
        # which puts it into validated_data automatically.
        return data

    def create(self, validated_data):
        section_obj = validated_data['section_obj']
        day = validated_data['day']
        periods_data = validated_data['timetable']
        standard_obj = validated_data['standard_obj']
        
        # This comes from the View: serializer.save(academic_year=active_year)
        active_year = validated_data.get('academic_year') 

        created_slots = []

        for period in periods_data:
            request = self.context.get("request")
            teacher_obj = scoped_teachers(request).get(teacher_id=period['teacher_id'])
            subject_obj = Subject.objects.get(standard=standard_obj, name__iexact=period['subject'])

            # Year-Safe Update or Create
            slot, created = TimetableSlot.objects.update_or_create(
                section=section_obj,
                day=day,
                period_no=period['period_no'],
                academic_year=active_year, # <--- Ensure we slot it into the correct year
                defaults={
                    'start_time': period['start_time'],
                    'end_time': period['end_time'],
                    'subject': subject_obj,
                    'teacher': teacher_obj
                }
            )
            created_slots.append(slot)

        return created_slots

# 3. Read Serializer for "My Timetable" View
class MyTimetableSerializer(serializers.ModelSerializer):
    # Flatten relationships to make it easy for frontend
    class_name = serializers.CharField(source='section.standard.name')
    section_name = serializers.CharField(source='section.name')
    subject_name = serializers.CharField(source='subject.name')

    class Meta:
        model = TimetableSlot
        fields = [
            'day', 
            'period_no', 
            'start_time', 
            'end_time', 
            'class_name', 
            'section_name', 
            'subject_name'
        ]

# 4. Substitution Create Serializer
class SubstitutionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Substitution
        fields = ['slot', 'date', 'substitute_teacher', 'subject']

class SubstitutionInputSerializer(serializers.Serializer):
    # Matches the JSON you want to send
    period_no = serializers.IntegerField()
    subject = serializers.CharField()     # e.g., "chemistry"
    teacher_id = serializers.CharField()  # e.g., "2003"
    
    # Optional fields
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)



# timetable/serializers.py

from .models import ClassBreak

class ClassBreakSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(write_only=True) # Matches frontend input

    class Meta:
        model = ClassBreak
        fields = ['id', 'class_name', 'name', 'start_time', 'end_time']

    def validate(self, data):
        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError("End time must be after start time.")
        
        try:
            request = self.context.get("request")
            standard = get_scoped_standard(request, data['class_name'])
            data['standard'] = standard
        except Standard.DoesNotExist:
            raise serializers.ValidationError("Invalid Class Name.")
        
        return data

    def create(self, validated_data):
        validated_data.pop('class_name')
        return ClassBreak.objects.create(**validated_data)


class TimetableAutoGenerateSerializer(serializers.Serializer):
    class_name = serializers.CharField()
    section = serializers.CharField(required=False)
    sections = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=False,
    )
    section_subject_teachers = serializers.DictField(
        child=serializers.DictField(child=serializers.CharField()),
        required=False
    )
    days = serializers.ListField(
        child=serializers.ChoiceField(choices=TimetableSlot.DAYS_OF_WEEK),
        required=True,
        allow_empty=False,
    )
    day_period_counts = serializers.DictField(
        child=serializers.IntegerField(min_value=1),
        required=True
    )
    max_subject_periods_per_day = serializers.IntegerField(min_value=1, required=True)
    support_combined_class = serializers.BooleanField(required=False, default=False)
    period_duration_minutes = serializers.IntegerField(min_value=1, required=True)
    first_period_start_time = serializers.TimeField(required=True)
    subject_periods = serializers.DictField(
        child=serializers.IntegerField(min_value=0),
        required=True
    )
    overwrite_existing = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        class_name = attrs["class_name"]

        section_name = attrs.get("section")
        sections_list = attrs.get("sections")
        if bool(section_name) == bool(sections_list):
            raise serializers.ValidationError(
                "Provide exactly one of 'section' or 'sections'."
            )

        try:
            request = self.context.get("request")
            standard_obj = get_scoped_standard(request, class_name)
        except Standard.DoesNotExist:
            raise serializers.ValidationError(f"Invalid Class {class_name}.")

        section_obj = None
        section_objs = None
        if section_name:
            try:
                section_obj = get_scoped_section(request, standard_obj, section_name)
            except Section.DoesNotExist:
                raise serializers.ValidationError(
                    f"Invalid Section {section_name} for Class {class_name}."
                )
        else:
            normalized = [str(name).strip() for name in sections_list or []]
            if not normalized:
                raise serializers.ValidationError("'sections' must not be empty.")
            if len(set(normalized)) != len(normalized):
                raise serializers.ValidationError("'sections' contains duplicates.")
            section_qs = scoped_sections(request).filter(standard=standard_obj, name__in=normalized)
            section_objs = list(section_qs)
            found = {s.name for s in section_objs}
            missing = [name for name in normalized if name not in found]
            if missing:
                raise serializers.ValidationError(
                    f"Invalid Section(s) for Class {class_name}: {', '.join(missing)}."
                )

        days = attrs["days"]
        day_period_counts = attrs["day_period_counts"]

        allowed_days = {d[0] for d in TimetableSlot.DAYS_OF_WEEK}
        provided_count_days = set(day_period_counts.keys())
        selected_days = set(days)

        invalid_keys = provided_count_days - allowed_days
        if invalid_keys:
            raise serializers.ValidationError(
                f"Invalid day keys in day_period_counts: {', '.join(sorted(invalid_keys))}."
            )

        missing_days = selected_days - provided_count_days
        if missing_days:
            raise serializers.ValidationError(
                f"Missing day_period_counts for: {', '.join(sorted(missing_days))}."
            )

        extra_days = provided_count_days - selected_days
        if extra_days:
            raise serializers.ValidationError(
                f"day_period_counts contains unselected days: {', '.join(sorted(extra_days))}."
            )

        subject_periods = attrs.get("subject_periods") or {}
        max_subject_periods_per_day = int(attrs.get("max_subject_periods_per_day", 1))
        valid_subject_names = set(
            Subject.objects.filter(standard=standard_obj).values_list('name', flat=True)
        )
        if not valid_subject_names:
            raise serializers.ValidationError(f"No subjects configured for Class {class_name}.")

        invalid_subjects = [name for name in subject_periods.keys() if name not in valid_subject_names]
        if invalid_subjects:
            raise serializers.ValidationError(
                f"Invalid subject name(s) in subject_periods: {', '.join(sorted(invalid_subjects))}."
            )

        missing_subjects = sorted(list(valid_subject_names - set(subject_periods.keys())))
        if missing_subjects:
            raise serializers.ValidationError(
                f"Missing subject count(s) for: {', '.join(missing_subjects)}."
            )

        max_per_subject_week = max_subject_periods_per_day * len(days)
        for subject_name, count in subject_periods.items():
            safe_count = int(count)
            if safe_count > max_per_subject_week:
                raise serializers.ValidationError(
                    f"Subject '{subject_name}' exceeds max weekly limit of {max_per_subject_week} "
                    f"with per-day max {max_subject_periods_per_day}."
                )

        section_subject_teachers = attrs.get("section_subject_teachers") or {}
        cleaned_section_subject_teachers: Dict[str, Dict[str, int]] = {}
        if section_subject_teachers:
            try:
                active_year = get_scoped_active_year(request)
                if not active_year:
                    raise AcademicYear.DoesNotExist
            except AcademicYear.DoesNotExist:
                raise serializers.ValidationError("No Active Academic Year found.")

            allowed_section_names = []
            if section_obj is not None:
                allowed_section_names = [section_obj.name]
            elif section_objs is not None:
                allowed_section_names = [s.name for s in section_objs]

            for section_name, subject_map in section_subject_teachers.items():
                if section_name not in allowed_section_names:
                    raise serializers.ValidationError(
                        f"Invalid section in section_subject_teachers: {section_name}."
                    )
                if not isinstance(subject_map, dict):
                    raise serializers.ValidationError(
                        f"Invalid subject mapping for section {section_name}."
                    )

                for subject_name, teacher_identifier in subject_map.items():
                    if not teacher_identifier:
                        continue
                    if subject_name not in valid_subject_names:
                        raise serializers.ValidationError(
                            f"Invalid subject '{subject_name}' in section_subject_teachers."
                        )

                    teacher = scoped_teachers(request).filter(teacher_id=str(teacher_identifier)).first()
                    if not teacher:
                        raise serializers.ValidationError(
                            f"Teacher ID {teacher_identifier} not found for section {section_name}."
                        )

                    is_allocated = TeacherAllocation.objects.filter(
                        academic_year=active_year,
                        teacher=teacher,
                        subject__name=subject_name,
                        standard=standard_obj
                    ).exists()
                    if not is_allocated:
                        raise serializers.ValidationError(
                            f"{teacher.teacher_id} {teacher.name} is not allocated to "
                            f"teach {subject_name} for Class {class_name}."
                        )

                    cleaned_section_subject_teachers.setdefault(section_name, {})[subject_name] = teacher.id

        attrs["standard_obj"] = standard_obj
        if section_obj is not None:
            attrs["section_obj"] = section_obj
        if section_objs is not None:
            attrs["section_objs"] = section_objs
        if cleaned_section_subject_teachers:
            attrs["section_subject_teacher_ids"] = cleaned_section_subject_teachers
        return attrs
