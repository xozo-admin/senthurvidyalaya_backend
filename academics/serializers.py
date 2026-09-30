from rest_framework import serializers
from .models import Standard, Section
from school.tenant import get_requested_school

# ==========================================
# 1. SECTION SERIALIZER (Simplified)
# ==========================================
class SectionSerializer(serializers.ModelSerializer):
    # Shows "10" instead of just ID 1 when viewing a section
    standard_name = serializers.CharField(source='standard.name', read_only=True)
    
    class Meta:
        model = Section
        # Removed 'class_teacher' entirely as requested
        fields = ['id', 'school', 'name', 'standard', 'standard_name']
        read_only_fields = ['school']

# ==========================================
# 2. STANDARD SERIALIZER
# ==========================================
class StandardSerializer(serializers.ModelSerializer):
    # Nested serializer: When you view Class 10, it shows [A, B, C] automatically
    sections = SectionSerializer(many=True, read_only=True)

    class Meta:
        model = Standard
        fields = ['id', 'school', 'name', 'description', 'sections']
        read_only_fields = ['school']


# ==========================================
# 3. NEW SERIALIZER (For Setup/Creation)
# ==========================================
class SectionSetupSerializer(serializers.ModelSerializer):
    """
    Used ONLY for the Setup API. 
    Accepts text inputs like "11" and "B" to create/find the Class and Section.
    """
    # 1. We accept text input (e.g., "11") instead of an ID
    class_name = serializers.CharField(write_only=True)
    
    # 2. We map "section_name" input to the "name" field of the model
    section_name = serializers.CharField(source='name', write_only=True)

    class Meta:
        model = Section
        # We only return the ID and Name after creation
        fields = ['class_name', 'section_name', 'id', 'name']
        read_only_fields = ['id', 'name']

    def create(self, validated_data):
        # Extract the text inputs
        class_text = validated_data.pop('class_name')
        section_text = validated_data.pop('name') # Source is 'name'

        # 1. Get or Create the Class (Standard)
        request = self.context.get("request")
        school = get_requested_school(request) if request else None
        if not school:
            raise serializers.ValidationError("Select a school before creating sections.")

        standard_obj, _ = Standard.objects.get_or_create(
            school=school,
            name=class_text,
        )

        # 2. Get or Create the Section linked to that Class
        section_obj, created = Section.objects.get_or_create(
            standard=standard_obj,
            name=section_text,
            defaults={"school": school},
        )
        if school and not section_obj.school_id:
            section_obj.school = school
            section_obj.save(update_fields=["school"])
        
        return section_obj

        # ... existing imports ...

# --- NEW: Simple Serializer for Dropdowns / Lists ---
class SimpleStandardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Standard
        fields = ['id', 'school', 'name']  # No 'sections' field here!
        read_only_fields = ['school']
