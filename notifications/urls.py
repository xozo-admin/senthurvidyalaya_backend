from django.urls import path
from .views import MyNotificationView, RegisterDeviceTokenView, WhatsAppAlertSettingsView

urlpatterns = [
    path('', MyNotificationView.as_view(), name='my-notifications'),
    # ... your other urls ...
    path('register-device/', RegisterDeviceTokenView.as_view(), name='register-device'),
    path('whatsapp-alert-settings/', WhatsAppAlertSettingsView.as_view(), name='whatsapp-alert-settings'),
]
