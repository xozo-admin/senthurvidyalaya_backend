from rest_framework import serializers
from .models import Assignment, AssignmentSubmission

class AssignmentSerializer(serializers.ModelSerializer):
    # Show Teacher's Name instead of ID
    posted_by = serializers.CharField(source='teacher.teacher_profile.name', read_only=True)
    
    # NEW: Show the Academic Year Name (e.g., "2025-2026")
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = Assignment
        fields = [
            'id', 'class_name', 'section', 'subject', 'title', 
            'description', 'due_date', 'attachment', 'posted_by', 
            'created_at', 'academic_year' # <--- Added here
        ]
        # 'academic_year' is read-only because the VIEW injects the Active Year automatically
        read_only_fields = ['teacher', 'created_at', 'academic_year']

class SubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.username', read_only=True)
    subject_name = serializers.CharField(source='assignment.subject', read_only=True) 
    assignment_title = serializers.CharField(source='assignment.title', read_only=True) 

    class Meta:
        model = AssignmentSubmission
        fields = [
            'id', 'assignment', 'assignment_title', 'subject_name', 
            'student_name', 'description', 'file', 'submitted_at', 'marks'
        ]
        read_only_fields = ['marks', 'graded_by', 'submitted_at']

class StudentFeedSerializer(serializers.ModelSerializer):
    posted_by = serializers.CharField(source='teacher.teacher_profile.name', read_only=True)
    status = serializers.SerializerMethodField()
    
    # NEW: Context for the Student
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = Assignment
        fields = [
            'id', 'class_name', 'section', 'subject', 'title', 
            'description', 'due_date', 'attachment', 'posted_by', 'created_at',
            'status', 'academic_year' # <--- Added here
        ]

    def get_status(self, obj):
        # Access the logged-in user from the context
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            # Check if a submission exists for this specific assignment & student
            has_submitted = obj.submissions.filter(student=request.user).exists()
            return "Submitted" if has_submitted else "Pending"
        return "Pending"


class StudentAssignmentCombinedSerializer(serializers.ModelSerializer):
    posted_by = serializers.CharField(source='teacher.teacher_profile.name', read_only=True)
    academic_year = serializers.CharField(source='academic_year.name', read_only=True)
    status = serializers.SerializerMethodField()
    submission = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = [
            'id', 'class_name', 'section', 'subject', 'title',
            'description', 'due_date', 'attachment', 'posted_by', 'created_at',
            'status', 'academic_year', 'submission'
        ]

    def get_status(self, obj):
        submissions = getattr(obj, 'student_submissions', None)
        if submissions:
            return "Submitted"
        return "Pending"

    def get_submission(self, obj):
        submissions = getattr(obj, 'student_submissions', None)
        if not submissions:
            return None
        return SubmissionSerializer(submissions[0]).data
