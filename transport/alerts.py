import logging

from django.utils import timezone

from notifications.whatsapp import send_whatsapp_template, whatsapp_alert_enabled

from .models import Stop, TransportAllocation, TransportWhatsAppAlertLog


logger = logging.getLogger(__name__)


def guess_trip_type():
    return 'Morning' if timezone.localtime().hour < 12 else 'Evening'


def passenger_label(allocation):
    if allocation.student:
        return allocation.student.student_name
    if allocation.teacher:
        return allocation.teacher.name
    if allocation.staff:
        return allocation.staff.name
    return 'Passenger'


def passenger_phone(allocation):
    if allocation.student:
        return allocation.student.father_phone or allocation.student.mother_phone or ''
    if allocation.teacher:
        return allocation.teacher.phone or ''
    if allocation.staff:
        return allocation.staff.phone or ''
    return ''


def passenger_allocations_for_vehicle(vehicle, trip_type=None):
    queryset = TransportAllocation.objects.filter(vehicle=vehicle).select_related(
        'student',
        'teacher',
        'staff',
        'stop',
    )
    if trip_type in {'Morning', 'Evening'}:
        queryset = queryset.filter(stop__trip_type=trip_type)
    return queryset


def create_or_get_transport_whatsapp_log(
    allocation,
    route,
    alert_type,
    message,
    trip_type,
    stop=None,
    sent_to='',
):
    return TransportWhatsAppAlertLog.objects.get_or_create(
        allocation=allocation,
        route=route,
        stop=stop,
        alert_type=alert_type,
        trip_type=trip_type,
        alert_date=timezone.localdate(),
        defaults={
            'sent_to': sent_to,
            'message': message,
        },
    )


def send_transport_whatsapp_once(allocation, route, alert_type, message, trip_type, stop=None):
    if not whatsapp_alert_enabled('transport'):
        return False, {'error': 'WhatsApp alert disabled: transport'}

    phone = passenger_phone(allocation)
    if not phone:
        return False, {'error': 'Passenger phone number is missing.'}

    log, created = create_or_get_transport_whatsapp_log(
        allocation=allocation,
        route=route,
        stop=stop,
        alert_type=alert_type,
        message=message,
        trip_type=trip_type,
        sent_to=phone,
    )
    if not created:
        return False, {'error': 'Transport WhatsApp alert already sent today.'}

    ok, response = send_whatsapp_template(phone, message, alert_type='transport')
    log.status = 'SENT' if ok else 'FAILED'
    log.response_data = response
    log.save(update_fields=['status', 'response_data'])

    if not ok:
        logger.warning("Transport WhatsApp alert failed for allocation %s: %s", allocation.id, response)

    return ok, response


def send_transport_trip_started_whatsapp(route, trip_type=None):
    trip_type = trip_type if trip_type in {'Morning', 'Evening'} else guess_trip_type()
    vehicle = route.vehicle
    driver_name = vehicle.driver.name if vehicle.driver else 'Driver'
    allocations = passenger_allocations_for_vehicle(vehicle)

    summary = {'sent': 0, 'failed': 0, 'skipped': 0}
    for allocation in allocations:
        message = (
            f"Transport alert: Bus {vehicle.bus_number} {trip_type.lower()} trip has started. "
            f"Driver: {driver_name}. Please track the bus live."
        )
        ok, response = send_transport_whatsapp_once(
            allocation=allocation,
            route=route,
            alert_type=TransportWhatsAppAlertLog.ALERT_BUS_STARTED,
            message=message,
            trip_type=trip_type,
        )
        if ok:
            summary['sent'] += 1
        elif response.get('error') in {
            'Transport WhatsApp alert already sent today.',
            'WhatsApp alert disabled: transport',
        }:
            summary['skipped'] += 1
        else:
            summary['failed'] += 1
    return summary


def send_transport_arrival_whatsapp(allocation, route, stop, distance_km):
    trip_type = stop.trip_type if stop and stop.trip_type in {'Morning', 'Evening'} else guess_trip_type()
    message = (
        f"Transport alert: Bus {route.vehicle.bus_number} is within 1 km of "
        f"{stop.stop_name} for {passenger_label(allocation)}. Please be ready."
    )
    return send_transport_whatsapp_once(
        allocation=allocation,
        route=route,
        stop=stop,
        alert_type=TransportWhatsAppAlertLog.ALERT_NEAR_STOP,
        message=message,
        trip_type=trip_type,
    )
