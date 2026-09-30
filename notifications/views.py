from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import Notification, WhatsAppAlertSetting
from .serializers import NotificationSerializer, WhatsAppAlertSettingSerializer
from .models import FCMDevice

class MyNotificationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """ Get my notifications (Newest first) """
        # Only show notifications for the logged-in user
        notifs = Notification.objects.filter(recipient=request.user).order_by('-created_at')
        serializer = NotificationSerializer(notifs, many=True)
        return Response(serializer.data)

    def put(self, request):
        """ Mark as Read """
        notif_id = request.data.get('notification_id')
        
        if notif_id:
            # Mark ONE as read
            notif = get_object_or_404(Notification, id=notif_id, recipient=request.user)
            notif.is_read = True
            notif.save()
        else:
            # Mark ALL as read
            Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
            
        return Response({"message": "Updated successfully"})
        

## token saving view
class RegisterDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('fcm_token')
        
        if not token:
            return Response({"error": "fcm_token is required"}, status=400)

        # 1. Check if this exact physical phone token already exists in our DB
        device, created = FCMDevice.objects.get_or_create(
            fcm_token=token,
            defaults={'user': request.user} # If it's new, assign it to the logged-in user
        )

        # 2. What if Student A logs out, and Student B logs into the same phone?
        # We need to re-assign the phone's token to Student B.
        if not created and device.user != request.user:
            device.user = request.user
            device.save()

        return Response({"message": "Device registered for push notifications."}, status=200)


class WhatsAppAlertSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def _ensure_admin(self, request):
        return getattr(request.user, 'user_type', None) in ('admin', 'super_admin')

    def _ensure_defaults(self):
        for alert_type, _label in WhatsAppAlertSetting.ALERT_CHOICES:
            WhatsAppAlertSetting.objects.get_or_create(
                alert_type=alert_type,
                defaults={'enabled': True},
            )

    def get(self, request):
        if not self._ensure_admin(request):
            return Response({'error': 'Only admins can manage WhatsApp alert settings.'}, status=403)

        self._ensure_defaults()
        queryset = WhatsAppAlertSetting.objects.all()
        serializer = WhatsAppAlertSettingSerializer(queryset, many=True)
        return Response({'data': serializer.data}, status=200)

    def patch(self, request):
        if not self._ensure_admin(request):
            return Response({'error': 'Only admins can manage WhatsApp alert settings.'}, status=403)

        alert_type = request.data.get('alert_type')
        enabled = request.data.get('enabled')

        valid_alert_types = {choice[0] for choice in WhatsAppAlertSetting.ALERT_CHOICES}
        if alert_type not in valid_alert_types:
            return Response({'error': 'Invalid alert type.'}, status=400)
        if not isinstance(enabled, bool):
            return Response({'error': 'enabled must be true or false.'}, status=400)

        setting, _ = WhatsAppAlertSetting.objects.get_or_create(alert_type=alert_type)
        setting.enabled = enabled
        setting.save(update_fields=['enabled', 'updated_at'])

        serializer = WhatsAppAlertSettingSerializer(setting)
        return Response({'data': serializer.data}, status=200)
