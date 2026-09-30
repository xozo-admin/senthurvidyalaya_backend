from rest_framework import serializers
from .models import WorkOrder, WorkAssignment, RecurringTaskTemplate

class WorkAssignmentSerializer(serializers.ModelSerializer):
    # Read-only fields to show Staff details nicely in the API response
    staff_name = serializers.CharField(source='staff.name', read_only=True)
    staff_id = serializers.CharField(source='staff.staff_id', read_only=True)
    
    # Custom method to get full URL for the proof file
    proof_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkAssignment
        fields = [
            'id', 
            'staff_id', 
            'staff_name', 
            'status', 
            'proof_file', 
            'proof_url', 
            'completion_note', 
            'completed_at'
        ]

    def get_proof_url(self, obj):
        if obj.proof_file:
            return obj.proof_file.url
        return None

class WorkOrderSerializer(serializers.ModelSerializer):
    # This nests the assignments inside the Task
    # So when Admin views a task, they see the list of people assigned to it
    assignments = WorkAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            'id', 
            'description', 
            'staff_type', 
            'created_date', 
            'assignments'
        ]

class RecurringTaskTemplateSerializer(serializers.ModelSerializer):
    staff_id = serializers.CharField(source='staff.staff_id') # Accept ID (e.g., "3010")
    staff_name = serializers.CharField(source='staff.name', read_only=True)

    class Meta:
        model = RecurringTaskTemplate
        fields = ['id', 'day_of_week', 'staff_id', 'staff_name', 'description']
    
    def create(self, validated_data):
        # Handle looking up staff by string ID
        staff_data = validated_data.pop('staff')
        staff_id = staff_data.get('staff_id')
        
        from staff.models import NonTeachingStaff
        staff_obj = NonTeachingStaff.objects.get(staff_id=staff_id)
        
        return RecurringTaskTemplate.objects.create(staff=staff_obj, **validated_data)