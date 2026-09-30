# audit/serializers.py
from rest_framework import serializers
from .models import AuditLog
from django.contrib.auth import get_user_model

User = get_user_model()

class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_name', 'action', 'severity', 'details',
            'ip_address', 'user_agent', 'request_method', 'request_path',
            'response_status', 'timestamp', 'gateway_mode'
        ]
        read_only_fields = ['timestamp']
    
    def get_user_name(self, obj):
        if obj.user:
            return obj.user.email or obj.user.username
        return 'System'
