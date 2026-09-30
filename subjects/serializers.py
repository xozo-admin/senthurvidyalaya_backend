from rest_framework import serializers
from .models import Subject
from academics.models import Standard
from school.tenant import scope_queryset_for_user


def build_subject_code(subject_name, class_name):
    words = [word for word in ''.join(
        char if char.isalnum() else ' ' for char in subject_name
    ).upper().split() if word]
    if not words:
        prefix = 'SUB'
    elif len(words) == 1:
        prefix = words[0][:4]
    else:
        prefix = ''.join(word[0] for word in words[:4])
    return f"{prefix}-{class_name}".replace(' ', '')[:20]


def build_unique_subject_code(subject_name, class_name, standard=None):
    base_code = build_subject_code(subject_name, class_name)
    code = base_code
    counter = 2

    queryset = Subject.objects.filter(standard=standard) if standard else Subject.objects.all()
    while queryset.filter(subject_code=code).exists():
        suffix = f"-{counter}"
        code = f"{base_code[:20 - len(suffix)]}{suffix}"
        counter += 1

    return code


class BulkSubjectAssignSerializer(serializers.Serializer):
    class_name = serializers.CharField()
    subjects = serializers.ListField(child=serializers.CharField()) # List of strings ["Maths", "Science"]

    def create(self, validated_data):
        class_name = validated_data['class_name']
        subject_names = validated_data['subjects']

        # 1. Find the Class (Standard)
        request = self.context.get("request")
        try:
            standards = scope_queryset_for_user(Standard.objects.all(), request) if request else Standard.objects.all()
            standard_obj = standards.get(name=class_name)
        except (Standard.DoesNotExist, Standard.MultipleObjectsReturned):
            raise serializers.ValidationError(f"Class '{class_name}' does not exist.")

        created_subjects = []
        
        # 2. Create Subjects
        for subj_name in subject_names:
            code = build_unique_subject_code(subj_name, class_name, standard_obj)
            
            subject_obj, created = Subject.objects.get_or_create(
                name=subj_name,
                standard=standard_obj,
                defaults={'subject_code': code}
            )
            created_subjects.append(subject_obj)

        return created_subjects

class SubjectSerializer(serializers.ModelSerializer):
    standard_id = serializers.IntegerField(source='standard.id', read_only=True)
    standard_name = serializers.CharField(source='standard.name', read_only=True)

    class Meta:
        model = Subject
        fields = ['id', 'name', 'subject_code', 'standard_id', 'standard_name']
