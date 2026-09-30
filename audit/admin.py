# audit/admin.py
from django.contrib import admin
from .models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'action', 'severity', 'gateway_mode', 'ip_address']
    list_filter = ['action', 'severity', 'gateway_mode', 'timestamp']
    search_fields = ['user__email', 'ip_address', 'details']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('user', 'action', 'severity', 'timestamp')
        }),
        ('Request Details', {
            'fields': ('ip_address', 'user_agent', 'request_method', 'request_path', 'response_status')
        }),
        ('Payment Information', {
            'fields': ('gateway_mode', 'session_key')
        }),
        ('Additional Data', {
            'fields': ('details', 'browser_info')
        }),
    )

    