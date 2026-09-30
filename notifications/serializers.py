from rest_framework import serializers
from .models import Notification, WhatsAppAlertSetting

class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_id = serializers.SerializerMethodField()
    sender_type = serializers.SerializerMethodField() # Helpful for frontend (e.g., show Admin icon)

    class Meta:
        model = Notification
        fields = [
            'id', 
            'sender_id', 
            'sender_name', 
            'sender_type', # Added this for clarity
            'title', 
            'message', 
            'is_read', 
            'created_at', 
            'notification_type'
        ]

    def get_sender_name(self, obj):
        if not obj.sender:
            return "System"
        
        # Check profiles for real names
        if hasattr(obj.sender, 'teacher_profile'):
            return obj.sender.teacher_profile.name
        elif hasattr(obj.sender, 'student_profile'):
            return obj.sender.student_profile.student_name
        elif hasattr(obj.sender, 'staff_profile'):
            return obj.sender.staff_profile.name
        
        # Fallback for Admin (return username, e.g., "admin")
        return obj.sender.username 

    def get_sender_id(self, obj):
        if not obj.sender:
            return None
            
        # Check profiles for Custom IDs (TCH-01, etc.)
        if hasattr(obj.sender, 'teacher_profile'):
            return obj.sender.teacher_profile.teacher_id
        elif hasattr(obj.sender, 'student_profile'):
            return obj.sender.student_profile.student_id
        elif hasattr(obj.sender, 'staff_profile'):
            return obj.sender.staff_profile.staff_id
        
        # For Admin, return the Database ID (Integer)
        return obj.sender.id

    def get_sender_type(self, obj):
        """ Returns 'Admin', 'Teacher', 'Student', 'Staff' """
        if not obj.sender:
            return "System"
        return obj.sender.user_type.capitalize() # e.g., "Admin"


class WhatsAppAlertSettingSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source='get_alert_type_display', read_only=True)

    class Meta:
        model = WhatsAppAlertSetting
        fields = ['alert_type', 'label', 'enabled', 'updated_at']
        read_only_fields = ['alert_type', 'label', 'updated_at']
