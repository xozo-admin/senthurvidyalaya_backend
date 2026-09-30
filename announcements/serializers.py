from rest_framework import serializers
from .models import Announcement, StaffAnnouncement, CommonAnnouncement, TeacherAnnouncement
from academics.models import Standard, Section, ClassTeacher
from subjects.models import Subject
from timetable.models import TimetableSlot  # <--- KEY CHANGE: Permission Source
from school.models import AcademicYear 
from datetime import datetime


class AnnouncementCreateSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(write_only=True)
    section_name = serializers.CharField(write_only=True)
    subject_name = serializers.CharField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Announcement
        fields = [
            'class_name', 'section_name', 'subject_name', 
            'announcement_type', 'description'
        ]

    def validate(self, data):
        request = self.context.get('request')
        teacher = request.user.teacher_profile
        
        # 0. Get Active Year (Crucial for Permission Checks)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            raise serializers.ValidationError("No Active Academic Year Configured.")

        # 1. Resolve Class/Section
        try:
            standard = Standard.objects.get(name=data['class_name'])
            section = Section.objects.get(standard=standard, name=data['section_name'])
        except:
            raise serializers.ValidationError("Invalid Class or Section.")

        subject_name = data.get('subject_name')
        subject = None

        # A. Subject Teacher Logic
        if subject_name:
            try:
                subject = Subject.objects.get(standard=standard, name__iexact=subject_name)
            except Subject.DoesNotExist:
                raise serializers.ValidationError(f"Subject '{subject_name}' not found.")

            # --- STRICT PERMISSION CHECK (TIMETABLE) ---
            # Replaced 'TeacherAllocation' with 'TimetableSlot'
            # Logic: "Does this teacher have a slot for THIS Subject in THIS Section?"
            has_permission = TimetableSlot.objects.filter(
                academic_year=active_year,
                teacher=teacher,
                section=section,  # Strict Section Check
                subject=subject   # Strict Subject Check
            ).exists()

            if not has_permission:
                # Double Check: Are they the Class Teacher? (Class Teachers can sometimes post for subjects)
                # If your rule is "Class Teachers can post anything", uncomment the next 5 lines.
                # is_ct = ClassTeacher.objects.filter(
                #     teacher=teacher, section=section, academic_year=active_year
                # ).exists()
                # if not is_ct:
                     raise serializers.ValidationError(f"Permission Denied: You are not scheduled to teach {subject_name} to Class {data['class_name']}-{data['section_name']}.")

        # B. Class Teacher Logic (No Subject Name provided)
        else:
            # YEAR-SAFE Permission Check
            is_class_teacher = ClassTeacher.objects.filter(
                teacher=teacher,
                section=section,
                academic_year=active_year
            ).exists()

            if not is_class_teacher:
                raise serializers.ValidationError(f"Only the assigned Class Teacher for {active_year.name} can post generic announcements.")

        data['section_obj'] = section
        data['subject_obj'] = subject
        data['teacher_obj'] = teacher
        data['academic_year'] = active_year # Pass to create()
        return data

    def create(self, validated_data):
        teacher = validated_data['teacher_obj']
        
        # 1. Get Injected Year
        active_year = validated_data['academic_year'] 

        # 2. Auto-calculate Number (Per Teacher, Per Day)
        today = datetime.now().date()
        count = Announcement.objects.filter(teacher=teacher, created_at__date=today).count()

        return Announcement.objects.create(
            academic_year=active_year, # <--- Saved here
            teacher=teacher,
            section=validated_data['section_obj'],
            subject=validated_data['subject_obj'],
            announcement_type=validated_data['announcement_type'],
            description=validated_data['description'],
            announcement_number=count + 1
        )

# announcements/serializers.py

class AnnouncementListSerializer(serializers.ModelSerializer):
    posted_by = serializers.CharField(source='teacher.name')
    subject_name = serializers.SerializerMethodField()
    posted_time = serializers.SerializerMethodField()
    
    # NEW FIELDS: To identify the class & year
    class_name = serializers.CharField(source='section.standard.name')
    section_name = serializers.CharField(source='section.name')
    academic_year = serializers.CharField(source='academic_year.name', read_only=True) 

    class Meta:
        model = Announcement
        fields = [
            'announcement_number', 
            'posted_by', 
            'class_name',    
            'section_name',  
            'subject_name', 
            'announcement_type', 
            'description', 
            'posted_time',
            'academic_year' 
        ]

    def get_subject_name(self, obj):
        return obj.subject.name if obj.subject else "General"

    def get_posted_time(self, obj):
        return obj.created_at.strftime("%I:%M %p")


class AnnouncementListWithDateSerializer(AnnouncementListSerializer):
    posted_date = serializers.SerializerMethodField()

    class Meta(AnnouncementListSerializer.Meta):
        fields = AnnouncementListSerializer.Meta.fields + ['posted_date']

    def get_posted_date(self, obj):
        return obj.created_at.strftime("%Y-%m-%d")


# ==========================================
#  NEW: STAFF ANNOUNCEMENT SERIALIZER
# ==========================================
class StaffAnnouncementSerializer(serializers.ModelSerializer):
    academic_year = serializers.CharField(source='academic_year.name', read_only=True) 

    class Meta:
        model = StaffAnnouncement
        fields = '__all__'
        read_only_fields = ['academic_year'] # Injected by View

# --- NEW SERIALIZER ---
class CommonAnnouncementSerializer(serializers.ModelSerializer):
    academic_year = serializers.CharField(source='academic_year.name', read_only=True) 

    class Meta:
        model = CommonAnnouncement
        fields = '__all__'
        read_only_fields = ['academic_year'] # Injected by View


class CommonAnnouncementWithDateSerializer(CommonAnnouncementSerializer):
    posted_date = serializers.SerializerMethodField()

    class Meta(CommonAnnouncementSerializer.Meta):
        fields = '__all__'

    def get_posted_date(self, obj):
        return obj.date.strftime("%Y-%m-%d") if obj.date else None


# announcements/serializers.py

class TeacherAnnouncementSerializer(serializers.ModelSerializer):
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = TeacherAnnouncement
        fields = '__all__'
        read_only_fields = ['academic_year', 'created_at']
