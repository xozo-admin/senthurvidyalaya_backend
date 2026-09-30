from rest_framework import serializers
from .models import ClassResource

class ClassResourceSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    posted_by_name = serializers.CharField(source='posted_by.teacher_profile.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    class_name = serializers.CharField(source='section.standard.name', read_only=True)
    
    # NEW: Context for the Academic Year
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = ClassResource
        fields = [
            'id', 'section','section_name', 'class_name', 'subject', 'subject_name', 
            'title', 'description', 'file', 'posted_by_name', 
            'created_at', 'academic_year' # <--- Added here
        ]
        read_only_fields = ['section', 'posted_by', 'created_at', 'academic_year']