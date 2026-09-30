from rest_framework import serializers
from .models import BehaviorReport
from exams.models import ExamTerm

class PostBehaviorSerializer(serializers.ModelSerializer):
    # --- LOGIC INJECTION: YEAR-SAFE MAPPING ---
    # Old: source='student.username'
    # New: Traverse through Enrollment to find the student ID
    student_id = serializers.CharField(source='enrollment.student.student_id', read_only=True)
    
    # Input: "Term 1" (String) -> Output: ExamTerm (Object)
    term = serializers.CharField(write_only=True)
    term_name = serializers.CharField(source='term.name', read_only=True)

    class Meta:
        model = BehaviorReport
        fields = [
            'student_id', 'term', 'term_name', 
            'participation', 'responsibility', 'discipline', 
            'attitude', 'collaboration', 'remarks'
        ]

    def create(self, validated_data):
        # Extract "Term 1" string
        term_name = validated_data.pop('term')
        
        # Fetch Academic Year injected from the View's context
        active_year = self.context.get('academic_year')

        # STRICT LOOKUP: Get the term for this specific academic year
        term_obj = ExamTerm.objects.get(
            name__iexact=term_name,
            academic_year=active_year
        )
        
        # Save (Note: This assumes 'enrollment' is passed in validated_data if used)
        report = BehaviorReport.objects.create(term=term_obj, **validated_data)
        return report

class DashboardReportSerializer(serializers.Serializer):
    # (Unchanged)
    student_id = serializers.CharField()
    name = serializers.CharField()
    summative_total = serializers.CharField()
    overall_grade = serializers.CharField()
    overall_behaviour = serializers.CharField()
    behaviour_type = serializers.CharField()