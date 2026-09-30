from django.contrib import admin
from .models import FCMDevice, Notification, WhatsAppAlertSetting

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'recipient', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('title', 'message', 'recipient__username')


@admin.register(FCMDevice)
class FCMDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at')
    search_fields = ('user__username', 'fcm_token')


@admin.register(WhatsAppAlertSetting)
class WhatsAppAlertSettingAdmin(admin.ModelAdmin):
    list_display = ('alert_type', 'enabled', 'updated_at')
    list_editable = ('enabled',)
