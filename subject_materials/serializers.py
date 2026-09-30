from rest_framework import serializers
from .models import SubjectMaterial

class SubjectMaterialSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    posted_by = serializers.CharField(source='teacher.teacher_profile.name', read_only=True)
    
    # NEW: Include Academic Year in the API response
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = SubjectMaterial
        fields = [
            'id', 'class_name', 'section', 'subject', 'subject_name',
            'title', 'description', 'file', 'posted_by', 
            'created_at', 'academic_year' # <--- Added here
        ]
        read_only_fields = ['teacher', 'created_at', 'subject', 'academic_year']