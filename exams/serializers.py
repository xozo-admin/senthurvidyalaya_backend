from rest_framework import serializers
from .models import StudentMark, ExamSchedule, ExamDetail, ExamType, ExamTerm
from academics.models import Standard
from subjects.models import Subject
from school.models import AcademicYear  # <--- NEW IMPORT

# ==========================================
# 1. ADMIN: EXAM TYPE MANAGEMENT
# ==========================================
class ExamTypeSerializer(serializers.ModelSerializer):
    term_name = serializers.CharField(source='term.name', read_only=True)
    
    # We define 'term' manually to force Django to ignore DB rules during validation
    term = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ExamType
        fields = ['id', 'name', 'term', 'term_name', 'max_marks', 'rank']

# --- 2. PARENT SERIALIZER (The Term) ---
class ExamTermSerializer(serializers.ModelSerializer):
    exams = ExamTypeSerializer(many=True)

    class Meta:
        model = ExamTerm
        fields = ['id', 'name', 'rank', 'exams']

    def create(self, validated_data):
        # --- 1. ADDED: Fetch Active Year ---
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            raise serializers.ValidationError("Cannot create Term: No Active Academic Year set.")

        exams_data = validated_data.pop('exams')
        
        # --- 2. CHANGED: Inject academic_year into the Term creation ---
        term = ExamTerm.objects.create(academic_year=active_year, **validated_data)
        
        # 3. Create Nested Exams
        for exam_data in exams_data:
            ExamType.objects.create(term=term, **exam_data)
            
        return term


# ==========================================
# 3. ADMIN: EXAM SCHEDULING (Year-Aware & Complex Logic Preserved)
# ==========================================
class ExamDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamDetail
        fields = ['id', 'subject_name', 'exam_date', 'duration', 'session']

class ExamScheduleSerializer(serializers.ModelSerializer):
    # INPUT: ID | OUTPUT: Name
    exam_type_id = serializers.PrimaryKeyRelatedField(
        queryset=ExamType.objects.all(), source='exam_type', write_only=True
    )
    exam_type_name = serializers.CharField(source='exam_type.name', read_only=True)
    
    class_names = serializers.ListField(child=serializers.CharField(), write_only=True)
    classes = serializers.StringRelatedField(many=True, read_only=True)
    subjects = ExamDetailSerializer(many=True, source='details')

    class Meta:
        model = ExamSchedule
        fields = [
            'id', 'exam_type_id', 'exam_type_name', 'start_date', 'end_date', 
            'class_names', 'classes', 'subjects', 'created_at'
        ]

    def to_representation(self, instance):
        """ Hides other classes if filtered by context """
        data = super().to_representation(instance)
        filter_class_name = self.context.get('filter_class')
        if filter_class_name:
            filtered_standards = instance.classes.filter(name=filter_class_name)
            data['classes'] = [str(s) for s in filtered_standards]
        return data

    def validate(self, data):
        """
        PERFORM GLOBAL LOGICAL CHECKS (Scoped to Active Year)
        """
        # 0. GET ACTIVE YEAR (Critical for validation scope)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            raise serializers.ValidationError("No Active Academic Year Configured.")

        start_date = data.get('start_date')
        end_date = data.get('end_date')
        exam_type = data.get('exam_type')  # This is the ExamType OBJECT
        class_names = data.get('class_names', [])
        subjects_data = data.get('details', []) 

        # --- ADD THIS NEW CHECK HERE ---
        # 0.5 Prevent Cross-Year Scheduling
        if exam_type and exam_type.term.academic_year != active_year:
            raise serializers.ValidationError({
                "exam_type": f"Error: '{exam_type.name}' belongs to a different academic year ({exam_type.term.academic_year.name})."
            })
        # -------------------------------

        # 1. Basic Date Check
        if start_date > end_date:
            raise serializers.ValidationError({"date_range": "Start Date cannot be after End Date."})

        # 2. Duplicate Subject Check within the PAYLOAD
        payload_subject_names = [item['subject_name'].lower() for item in subjects_data]
        if len(payload_subject_names) != len(set(payload_subject_names)):
            raise serializers.ValidationError({
                "duplicate_entry": "You have listed the same subject multiple times in this single request."
            })

        # 3. Get Standard Objects
        standards = Standard.objects.filter(name__in=class_names)
        
        if len(standards) != len(set(class_names)):
            found_names = set(s.name for s in standards)
            missing = set(class_names) - found_names
            raise serializers.ValidationError({
                "class_names": f"The following classes do not exist: {', '.join(missing)}"
            })

        # --- LOOP THROUGH EACH CLASS TO VALIDATE ---
        for standard in standards:
            # A. Get Valid Subjects for this Class
            valid_subjects = set(
                Subject.objects.filter(standard=standard).values_list('name', flat=True)
            )
            valid_subjects_lower = {s.lower() for s in valid_subjects}

            for sub_item in subjects_data:
                s_name = sub_item['subject_name']
                s_date = sub_item['exam_date']
                s_session = sub_item['session']

                # CHECK 4: Subject Validity
                if s_name.lower() not in valid_subjects_lower:
                    raise serializers.ValidationError({
                        "subject_validity": f"Subject '{s_name}' is not defined for Class '{standard.name}'. Please check Subject Master."
                    })

                # CHECK 5: Date Range
                if not (start_date <= s_date <= end_date):
                    raise serializers.ValidationError({
                        "exam_date": f"Exam '{s_name}' on {s_date} is outside the schedule range ({start_date} to {end_date})."
                    })

                # CHECK 6: SESSION CLASH (Time conflict) - SCOPED TO ACTIVE YEAR
                clashes = ExamDetail.objects.filter(
                    schedule__classes=standard,
                    schedule__academic_year=active_year,  # <--- NEW: SCOPE TO ACTIVE YEAR
                    exam_date=s_date,
                    session__iexact=s_session
                )
                if clashes.exists():
                    clashing_subject = clashes.first().subject_name
                    raise serializers.ValidationError({
                        "schedule_clash": f"Class '{standard.name}' already has an exam '{clashing_subject}' on {s_date} ({s_session}). Cannot schedule '{s_name}'."
                    })

                # CHECK 7: DUPLICATE SUBJECT CHECK (Updated for ExamType ID)
                # Check if 'Mathematics' is ALREADY scheduled for 'Quarterly' in THIS YEAR
                duplicate_subject = ExamDetail.objects.filter(
                    schedule__classes=standard,
                    schedule__academic_year=active_year,  # <--- NEW: SCOPE TO ACTIVE YEAR
                    schedule__exam_type=exam_type, 
                    subject_name__iexact=s_name
                )
                
                if duplicate_subject.exists():
                    existing_date = duplicate_subject.first().exam_date
                    raise serializers.ValidationError({
                        "duplicate_subject": f"Class '{standard.name}' already has a {exam_type.name} exam for '{s_name}' scheduled on {existing_date} in {active_year.name}. You cannot add it again."
                    })

        return data

    def create(self, validated_data):
        # 0. INJECT ACTIVE YEAR
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            raise serializers.ValidationError("Cannot create schedule: No Active Academic Year set.")

        class_names = validated_data.pop('class_names', [])
        subjects_data = validated_data.pop('details', [])

        # 1. Create Schedule with Year
        schedule = ExamSchedule.objects.create(
            academic_year=active_year, # <--- INJECTION
            **validated_data
        )
        
        standards = Standard.objects.filter(name__in=class_names)
        schedule.classes.set(standards)

        # 2. Create Details
        for sub_data in subjects_data:
            ExamDetail.objects.create(schedule=schedule, **sub_data)

        return schedule

    def update(self, instance, validated_data):
        if 'class_names' in validated_data:
            class_names = validated_data.pop('class_names')
            standards = Standard.objects.filter(name__in=class_names)
            instance.classes.set(standards)
        
        return super().update(instance, validated_data)


# ==========================================
# 4. ADMIN: UPDATE SINGLE SUBJECT (PUT)
# ==========================================
class ExamDetailUpdateSerializer(serializers.ModelSerializer):
    """
    Handles validation when updating a SINGLE subject (e.g. changing Math date).
    """
    class Meta:
        model = ExamDetail
        fields = ['id', 'subject_name', 'exam_date', 'duration', 'session']

    def validate(self, data):
        """
        Run Strict Checks: Date Range & Session Clashes
        """
        instance = self.instance # The existing ExamDetail object
        schedule = instance.schedule
        
        # 1. Get New or Existing values
        new_date = data.get('exam_date', instance.exam_date)
        new_session = data.get('session', instance.session)
        new_name = data.get('subject_name', instance.subject_name)

        # 2. Check Date Range
        if not (schedule.start_date <= new_date <= schedule.end_date):
            raise serializers.ValidationError({
                "exam_date": f"Date {new_date} is outside the schedule range ({schedule.start_date} to {schedule.end_date})."
            })

        # 3. Check Session Clashes - SCOPED TO SAME YEAR (via schedule relationship)
        # Note: 'schedule' object already belongs to a specific year, so related lookups respect that.
        linked_classes = schedule.classes.all()
        
        for standard in linked_classes:
            clashes = ExamDetail.objects.filter(
                schedule__classes=standard,
                schedule__academic_year=schedule.academic_year, # <--- EXPLICIT YEAR MATCH
                exam_date=new_date,
                session__iexact=new_session
            ).exclude(id=instance.id)

            if clashes.exists():
                clashing_sub = clashes.first().subject_name
                raise serializers.ValidationError({
                    "schedule_clash": f"Class '{standard.name}' already has '{clashing_sub}' on {new_date} ({new_session}). Cannot move '{new_name}' here."
                })

        return data