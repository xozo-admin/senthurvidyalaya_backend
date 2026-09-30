import logging
import re
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE_ALERT_MAP = {
    'Assignment': 'assignments',
    'Attendance': 'attendance',
    'Transport': 'transport',
    'Leave': 'leave',
    'Leave Request': 'leave',
    'Meeting': 'meetings',
    'Meeting Reminder': 'meetings',
    'Exam': 'exams',
    'Marks': 'exams',
    'Mark Change': 'exams',
}


def normalize_whatsapp_number(phone: str | None) -> str:
    digits = re.sub(r'\D', '', str(phone or ''))
    if not digits:
        return ''

    if len(digits) == 10:
        return f'91{digits}'

    return digits


def whatsapp_alerts_configured() -> bool:
    return bool(
        settings.WHATSAPP_ALERTS_ENABLED
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_ACCESS_TOKEN
    )


def whatsapp_alert_enabled(alert_type: str) -> bool:
    if not alert_type:
        return True

    try:
        from .models import WhatsAppAlertSetting

        setting, _ = WhatsAppAlertSetting.objects.get_or_create(
            alert_type=alert_type,
            defaults={'enabled': True},
        )
        return setting.enabled
    except Exception as exc:
        logger.warning('Could not read WhatsApp alert setting for %s: %s', alert_type, exc)
        return True


def resolve_alert_type(notification_type: str | None) -> str:
    value = str(notification_type or '').strip()
    if not value:
        return ''

    if value in NOTIFICATION_TYPE_ALERT_MAP:
        return NOTIFICATION_TYPE_ALERT_MAP[value]

    lowered = value.lower()
    if 'assignment' in lowered:
        return 'assignments'
    if 'attendance' in lowered:
        return 'attendance'
    if 'transport' in lowered or 'bus' in lowered:
        return 'transport'
    if 'leave' in lowered:
        return 'leave'
    if 'meeting' in lowered:
        return 'meetings'
    if 'exam' in lowered or 'mark' in lowered:
        return 'exams'
    if 'fee' in lowered:
        return 'fees'

    return ''


def send_whatsapp_template(
    to_phone: str | None,
    text: str,
    alert_type: str = '',
) -> tuple[bool, dict[str, Any]]:
    to_number = normalize_whatsapp_number(to_phone)
    alert_text = str(text or '').strip()

    if alert_type and not whatsapp_alert_enabled(alert_type):
        return False, {'error': f'WhatsApp alert disabled: {alert_type}'}

    if not to_number or not alert_text:
        return False, {'error': 'Missing WhatsApp recipient or message text.'}

    if not whatsapp_alerts_configured():
        return False, {'error': 'WhatsApp alerts are not configured.'}

    url = (
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/'
        f'{settings.WHATSAPP_PHONE_NUMBER_ID}/messages'
    )
    payload = {
        'messaging_product': 'whatsapp',
        'to': to_number,
        'type': 'template',
        'template': {
            'name': settings.WHATSAPP_TEMPLATE_NAME,
            'language': {'code': settings.WHATSAPP_TEMPLATE_LANGUAGE},
            'components': [
                {
                    'type': 'body',
                    'parameters': [
                        {
                            'type': 'text',
                            'text': alert_text[:1024],
                        }
                    ],
                }
            ],
        },
    }
    headers = {
        'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.WHATSAPP_REQUEST_TIMEOUT,
        )
        response_data = response.json() if response.content else {}
        if response.ok:
            return True, response_data

        logger.warning('WhatsApp alert failed: %s', response_data)
        return False, response_data
    except Exception as exc:
        logger.exception('WhatsApp alert error')
        return False, {'error': str(exc)}


def send_whatsapp_to_user(
    user,
    text: str,
    notification_type: str | None = None,
    alert_type: str = '',
) -> tuple[bool, dict[str, Any]]:
    phone = getattr(user, 'phone', None)
    resolved_alert_type = alert_type or resolve_alert_type(notification_type)
    return send_whatsapp_template(phone, text, alert_type=resolved_alert_type)
