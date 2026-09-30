from rest_framework import serializers
from .models import ToDoTask
from academics.models import Standard, Section
from subjects.models import Subject

class ToDoCreateSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(write_only=True)
    section_name = serializers.CharField(write_only=True)
    subject_name = serializers.CharField(write_only=True)

    class Meta:
        model = ToDoTask
        fields = [
            'class_name', 'section_name', 'subject_name', 
            'date', 'title', 'task_type', 'estimated_time', 
            'teacher_note', 'priority'
        ]

    def validate(self, data):
        # 1. Resolve Class, Section, Subject
        try:
            standard = Standard.objects.get(name=data['class_name'])
            section = Section.objects.get(standard=standard, name=data['section_name'])
        except:
            raise serializers.ValidationError("Invalid Class or Section")

        try:
            subject = Subject.objects.get(standard=standard, name__iexact=data['subject_name'])
        except Subject.DoesNotExist:
            raise serializers.ValidationError("Invalid Subject for this Class")

        # REMOVED: Redundant TeacherAllocation check.
        # The View now handles the strict "Year-Safe" permission check 
        # BEFORE this serializer is even called.
        
        # Pass objects to save method
        data['section_obj'] = section
        data['subject_obj'] = subject
        data['teacher_obj'] = self.context['request'].user.teacher_profile
        return data

    def create(self, validated_data):
        teacher = validated_data['teacher_obj']
        date = validated_data['date']
        
        # FIX: Explicitly grab the academic_year passed from the View
        # logic: serializer.save(academic_year=active_year) -> puts it in validated_data
        active_year = validated_data['academic_year'] 
        
        # 2. Auto-Calculate Task Number (Per Teacher, Per Day)
        existing_count = ToDoTask.objects.filter(teacher=teacher, date=date).count()
        next_number = existing_count + 1

        task = ToDoTask.objects.create(
            academic_year=active_year, # <--- Successfully injected now
            teacher=teacher,
            section=validated_data['section_obj'],
            subject=validated_data['subject_obj'],
            date=date,
            task_number=next_number,
            title=validated_data['title'],
            task_type=validated_data['task_type'],
            estimated_time=validated_data['estimated_time'],
            teacher_note=validated_data.get('teacher_note', ''),
            priority=validated_data['priority']
        )
        return task

class ToDoListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToDoTask
        fields = ['task_number', 'title', 'teacher_note', 'priority']

class ToDoDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToDoTask
        fields = [
            'task_number', 
            'title', 
            'task_type', 
            'estimated_time', 
            'teacher_note', 
            'priority'
        ]

class StudentTaskSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    academic_year = serializers.CharField(source='academic_year.name', read_only=True) # <--- Added context

    class Meta:
        model = ToDoTask
        fields = [
            'id', 
            'task_number', 
            'title', 
            'task_type', 
            'estimated_time', 
            'teacher_note', 
            'priority', 
            'subject_name', 
            'teacher_name',
            'academic_year'
        ]