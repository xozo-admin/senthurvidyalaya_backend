from .models import Notification, FCMDevice
from django.conf import settings
from .whatsapp import send_whatsapp_to_user


def _unique_users(users):
    unique = {}
    for user in users:
        if user and getattr(user, 'id', None):
            unique[user.id] = user
    return list(unique.values())


def _chunks(items, size=500):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def send_notification_to_users(users, title, message, notif_type="General", sender=None, data=None):
    """
    Efficiently creates notifications for a list of Users and sends Push Notifications.
    users: List of recipients
    sender: The User object who triggered this (Optional)
    """
    recipients = _unique_users(list(users))
    if not recipients:
        return

    # --- 1. SAVE TO DATABASE (Your Existing Logic) ---
    notifications = []

    for user in recipients:
        notifications.append(
            Notification(
                recipient=user,
                sender=sender,
                title=title,
                message=message,
                notification_type=notif_type
            )
        )

    Notification.objects.bulk_create(notifications)

    # --- 2. SEND FIREBASE PUSH NOTIFICATION ---
    if settings.FIREBASE_ENABLED:
        from firebase_admin import messaging

        try:
            devices = FCMDevice.objects.filter(user__in=recipients)
            tokens = list(devices.values_list('fcm_token', flat=True))

            payload_data = {
                "type": str(notif_type or "General"),
                "notification_type": str(notif_type or "General"),
                "title": str(title or ""),
                "body": str(message or ""),
            }
            if isinstance(data, dict):
                payload_data.update({str(key): str(value) for key, value in data.items() if value is not None})

            for token_batch in _chunks(tokens):
                message_payload = messaging.MulticastMessage(
                    notification=messaging.Notification(
                        title=title,
                        body=message,
                    ),
                    data=payload_data,
                    android=messaging.AndroidConfig(
                        priority='high',
                        notification=messaging.AndroidNotification(
                            channel_id='high_importance_channel',
                            default_sound=True,
                        ),
                    ),
                    apns=messaging.APNSConfig(
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(sound='default', badge=1),
                        ),
                    ),
                    tokens=token_batch,
                )

                response = messaging.send_each_for_multicast(message_payload)
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success:
                            bad_token = token_batch[idx]
                            FCMDevice.objects.filter(fcm_token=bad_token).delete()

        except Exception as e:
            print(f"Firebase Push Error: {e}")

    # --- 3. SEND WHATSAPP ALERTS WHEN CONFIGURED ---
    for user in recipients:
        try:
            send_whatsapp_to_user(user, f"{title}: {message}", notification_type=notif_type)
        except Exception as e:
            print(f"WhatsApp Alert Error for {getattr(user, 'id', 'unknown')}: {e}")
