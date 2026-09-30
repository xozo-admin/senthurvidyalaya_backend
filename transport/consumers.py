import asyncio
import json
import math
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.contrib.auth import get_user_model

# Import your models
from .models import Route, TransportAllocation, TransportArrivalAlertLog
from .alerts import send_transport_arrival_whatsapp, send_transport_trip_started_whatsapp
# Import your notification utility
from notifications.utils import send_notification_to_users 

User = get_user_model()
ADMIN_LIVE_GROUP = 'bus_admin_live'
ARRIVAL_ALERT_DISTANCE_KM = 1.0

class BusTrackingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        self.route_id = self.scope['url_route']['kwargs']['route_id']
        self.room_group_name = f'bus_{self.route_id}'

        # 1. AUTH CHECK
        if not self.user.is_authenticated:
            await self.close()
            return

        # 2. PERMISSION CHECK
        is_authorized = await self.check_permission(self.user, self.route_id)

        if is_authorized:
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            await self.accept()
            # Send latest known location immediately to new viewers (if available).
            latest_location = await self.get_latest_location(self.route_id)
            if latest_location:
                await self.send(text_data=json.dumps(latest_location))

            # --- NEW: IF DRIVER CONNECTS, MARK BUS AS ACTIVE & NOTIFY ---
            if await self.check_is_driver(self.user):
                await self.update_trip_status(self.route_id, is_active=True)
                # Calls the new dual-notification function
                await self.notify_trip_start(self.route_id) 
            # ------------------------------------------------------------
        else:
            await self.close()

    async def disconnect(self, close_code):
        # --- NEW: IF DRIVER DISCONNECTS, MARK BUS AS INACTIVE ---
        if self.user.is_authenticated and await self.check_is_driver(self.user):
             await self.update_trip_status(self.route_id, is_active=False)
        # --------------------------------------------------------

        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            # 3. SENDER CHECK: Non-driver viewers can only request latest snapshot.
            is_driver = await self.check_is_driver(self.user)
            if not is_driver:
                if data.get('type') == 'snapshot_request':
                    latest_location = await self.get_latest_location(self.route_id)
                    if latest_location:
                        await self.send(text_data=json.dumps(latest_location))
                    return

                await self.send(text_data=json.dumps({
                    'type': 'ws_error',
                    'message': 'Only transport driver role can send location updates.'
                }))
                return

            latitude = data.get('latitude')
            longitude = data.get('longitude')
            speed = data.get('speed')
            timestamp = data.get('timestamp')

            # Validate payload before writing/broadcasting.
            if latitude is None or longitude is None:
                await self.send(text_data=json.dumps({
                    'type': 'ws_error',
                    'message': 'latitude and longitude are required.'
                }))
                return

            # Save to DB
            await self.save_location_to_db(self.route_id, latitude, longitude, speed)
            arrival_alerts = await self.check_arrival_alerts(self.route_id, latitude, longitude)

            # Broadcast to Group
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'location_update',
                    'latitude': latitude,
                    'longitude': longitude,
                    'speed': speed,
                    'timestamp': timestamp
                }
            )

            # Broadcast same update to admin live stream group.
            route_meta = await self.get_route_meta(self.route_id)
            if route_meta:
                await self.channel_layer.group_send(
                    ADMIN_LIVE_GROUP,
                    {
                        'type': 'admin_location_update',
                        'bus_id': route_meta['bus_id'],
                        'bus_number': route_meta['bus_number'],
                        'route_id': route_meta['route_id'],
                        'driver_name': route_meta['driver_name'],
                        'latitude': latitude,
                        'longitude': longitude,
                        'speed': speed,
                        'timestamp': timestamp,
                    }
                )

            for alert in arrival_alerts:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'arrival_alert',
                        **alert,
                    }
                )

            # Send ACK to sender so frontend can confirm backend receipt.
            await self.send(text_data=json.dumps({
                'type': 'ws_ack',
                'status': 'received',
                'route_id': self.route_id,
                'timestamp': timestamp
            }))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'ws_error',
                'message': 'Invalid JSON payload.'
            }))
        except Exception as exc:
            await self.send(text_data=json.dumps({
                'type': 'ws_error',
                'message': f'WebSocket receive failed: {str(exc)}'
            }))

    async def location_update(self, event):
        await self.send(text_data=json.dumps({
            'latitude': event['latitude'],
            'longitude': event['longitude'],
            'speed': event['speed'],
            'timestamp': event['timestamp']
        }))

    async def arrival_alert(self, event):
        recipient_user_ids = event.get('recipient_user_ids') or []
        if self.user.id not in recipient_user_ids:
            return

        await self.send(text_data=json.dumps({
            'type': 'arrival_alert',
            'title': event.get('title'),
            'message': event.get('message'),
            'bus_number': event.get('bus_number'),
            'stop_name': event.get('stop_name'),
            'distance_km': event.get('distance_km'),
            'threshold_km': event.get('threshold_km'),
            'timestamp': event.get('timestamp'),
        }))

    # ==========================================
    # DATABASE HELPERS
    # ==========================================

    @database_sync_to_async
    def check_permission(self, user, route_id):
        # --- KEEP YOUR ORIGINAL PERMISSION LOGIC HERE ---
        # (This ensures security is not broken)
        
        # 1. Superuser
        if user.is_superuser: return True
        
        # 2. Check User Type/Role
        user_type = getattr(user, 'user_type', '').lower()
        role = getattr(user, 'role', '').lower()
        if user_type in ['admin', 'super_admin', 'school_admin'] or role in ['admin', 'super_admin', 'school_admin']:
            return True

        # 3. Staff Profile (Admin/Principal)
        if hasattr(user, 'staff_profile'):
            staff_role = str(user.staff_profile.role).lower()
            if staff_role in ['admin', 'principal']: return True

        # 4. Driver & Passenger Checks
        try:
            route = Route.objects.get(id=route_id)
            vehicle = route.vehicle

            # Driver Check
            if hasattr(user, 'staff_profile') and vehicle.driver == user.staff_profile:
                return True
            
            # Allocation Check (Student/Teacher/Staff)
            if hasattr(user, 'student_profile'):
                return TransportAllocation.objects.filter(student=user.student_profile, vehicle=vehicle).exists()
            elif hasattr(user, 'teacher_profile'):
                 return TransportAllocation.objects.filter(teacher=user.teacher_profile, vehicle=vehicle).exists()
            elif hasattr(user, 'staff_profile'):
                 return TransportAllocation.objects.filter(staff=user.staff_profile, vehicle=vehicle).exists()

        except:
            return False
            
        return False

    @database_sync_to_async
    def check_is_driver(self, user):
        if not hasattr(user, 'staff_profile'):
            return False

        role_value = str(getattr(user.staff_profile, 'role', '')).strip().lower()
        allowed_roles = {'transport_staff', 'transport staff', 'driver', 'transport'}
        return role_value in allowed_roles

    @database_sync_to_async
    def get_latest_location(self, route_id):
        try:
            route = Route.objects.get(id=route_id)
        except Route.DoesNotExist:
            return None

        if route.current_latitude is None or route.current_longitude is None:
            return None

        return {
            'latitude': route.current_latitude,
            'longitude': route.current_longitude,
            'speed': route.current_speed if route.current_speed is not None else 0,
            'timestamp': route.last_updated.isoformat() if route.last_updated else timezone.now().isoformat()
        }

    @database_sync_to_async
    def get_route_meta(self, route_id):
        try:
            route = Route.objects.select_related('vehicle__driver').get(id=route_id)
        except Route.DoesNotExist:
            return None

        return {
            'route_id': route.id,
            'bus_id': route.vehicle.id,
            'bus_number': route.vehicle.bus_number,
            'driver_name': route.vehicle.driver.name if route.vehicle.driver else 'Unassigned',
        }

    @database_sync_to_async
    def save_location_to_db(self, route_id, lat, lng, speed):
        try:
            route = Route.objects.get(id=route_id)
            route.current_latitude = lat
            route.current_longitude = lng
            route.current_speed = speed
            route.last_updated = timezone.now()
            route.save()
        except Route.DoesNotExist:
            pass

    @staticmethod
    def calculate_distance_km(lat1, lon1, lat2, lon2):
        radius_km = 6371
        d_lat = math.radians(float(lat2) - float(lat1))
        d_lon = math.radians(float(lon2) - float(lon1))
        lat1_rad = math.radians(float(lat1))
        lat2_rad = math.radians(float(lat2))

        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius_km * c

    @database_sync_to_async
    def check_arrival_alerts(self, route_id, lat, lng):
        try:
            route = Route.objects.select_related('vehicle').get(id=route_id)
        except Route.DoesNotExist:
            return []

        today = timezone.localdate()
        allocations = TransportAllocation.objects.filter(
            vehicle=route.vehicle,
            stop__isnull=False,
            stop__latitude__isnull=False,
            stop__longitude__isnull=False,
        ).select_related(
            'stop',
            'student__user',
            'teacher__user',
            'staff__user',
        )

        alerts = []
        for allocation in allocations:
            passenger_user = None
            passenger_name = ''

            if allocation.student and allocation.student.user:
                passenger_user = allocation.student.user
                passenger_name = allocation.student.student_name
            elif allocation.teacher and allocation.teacher.user:
                passenger_user = allocation.teacher.user
                passenger_name = allocation.teacher.name
            elif allocation.staff and allocation.staff.user:
                passenger_user = allocation.staff.user
                passenger_name = allocation.staff.name

            if not passenger_user and not (allocation.student or allocation.teacher or allocation.staff):
                continue

            stop = allocation.stop
            distance_km = self.calculate_distance_km(
                lat,
                lng,
                stop.latitude,
                stop.longitude,
            )

            if distance_km > ARRIVAL_ALERT_DISTANCE_KM:
                continue

            _, created = TransportArrivalAlertLog.objects.get_or_create(
                allocation=allocation,
                route=route,
                stop=stop,
                alert_date=today,
                threshold_km=ARRIVAL_ALERT_DISTANCE_KM,
                defaults={'distance_km': round(distance_km, 3)},
            )
            if not created:
                continue

            bus_number = route.vehicle.bus_number
            title = 'Bus arriving soon'
            message = (
                f"Bus {bus_number} is within 1 km of {stop.stop_name}. "
                "Please be ready at your stop."
            )

            if passenger_user:
                send_notification_to_users(
                    users=[passenger_user],
                    sender=self.user,
                    title=title,
                    message=message,
                    notif_type='Transport',
                )

            whatsapp_ok, whatsapp_response = send_transport_arrival_whatsapp(
                allocation=allocation,
                route=route,
                stop=stop,
                distance_km=distance_km,
            )

            alerts.append({
                'recipient_user_ids': [passenger_user.id] if passenger_user else [],
                'title': title,
                'message': message,
                'bus_number': bus_number,
                'stop_name': stop.stop_name,
                'passenger_name': passenger_name,
                'distance_km': round(distance_km, 2),
                'threshold_km': ARRIVAL_ALERT_DISTANCE_KM,
                'whatsapp_sent': whatsapp_ok,
                'whatsapp_error': whatsapp_response.get('error') if not whatsapp_ok else '',
                'timestamp': timezone.now().isoformat(),
            })

        return alerts

    @database_sync_to_async
    def update_trip_status(self, route_id, is_active):
        try:
            route = Route.objects.get(id=route_id)
            route.is_active = is_active
            route.save()
        except Route.DoesNotExist:
            pass

    # --- UPDATED NOTIFICATION LOGIC (ADMINS + USERS) ---
    @database_sync_to_async
    def notify_trip_start(self, route_id):
        try:
            # 1. Get Route & Vehicle Details
            route = Route.objects.select_related('vehicle__driver').get(id=route_id)
            vehicle = route.vehicle
            driver_name = vehicle.driver.name if vehicle.driver else "The Driver"
            bus_num = vehicle.bus_number

            # --- GROUP A: NOTIFY ADMINS (They see ALL buses) ---
            admins = User.objects.filter(user_type__in=['admin', 'super_admin'])
            if admins.exists():
                send_notification_to_users(
                    users=admins,
                    sender=self.user,
                    title="Bus Active",
                    message=f"Bus {bus_num} ({driver_name}) is now LIVE.",
                    notif_type="Transport"
                )

            # --- GROUP B: NOTIFY ASSIGNED PASSENGERS (They see ONLY their bus) ---
            # Find all students/staff/teachers allocated to THIS vehicle
            allocations = TransportAllocation.objects.filter(vehicle=vehicle).select_related(
                'student__user', 'staff__user', 'teacher__user'
            )

            passenger_users = []
            for alloc in allocations:
                # Add the User object associated with the profile
                if alloc.student and alloc.student.user:
                    passenger_users.append(alloc.student.user)
                elif alloc.teacher and alloc.teacher.user:
                    passenger_users.append(alloc.teacher.user)
                elif alloc.staff and alloc.staff.user:
                    passenger_users.append(alloc.staff.user)

            # Send Notification to unique users (set removes duplicates)
            if passenger_users:
                send_notification_to_users(
                    users=list(set(passenger_users)),
                    sender=self.user,
                    title="Your Bus Started",
                    message=f"Bus {bus_num} has started the trip. Track it live now!",
                    notif_type="Transport"
                )

            send_transport_trip_started_whatsapp(route)

        except Exception as e:
            print(f"Trip Notification Error: {e}")


class AdminLiveTrackingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        self.snapshot_task = None

        if not self.user.is_authenticated:
            await self.close()
            return

        is_admin_user = await self.check_is_admin(self.user)
        if not is_admin_user:
            await self.close()
            return

        await self.channel_layer.group_add(ADMIN_LIVE_GROUP, self.channel_name)
        await self.accept()
        await self.send(text_data=json.dumps({
            'type': 'admin_live_connected',
            'message': 'Connected to all active buses live stream.',
        }))
        snapshot = await self.get_active_bus_locations()
        await self.send(text_data=json.dumps({
            'type': 'admin_live_snapshot',
            'buses': snapshot,
        }))
        self.snapshot_task = asyncio.create_task(self.stream_periodic_snapshots())

    async def disconnect(self, close_code):
        if self.snapshot_task and not self.snapshot_task.done():
            self.snapshot_task.cancel()
            try:
                await self.snapshot_task
            except asyncio.CancelledError:
                pass
        await self.channel_layer.group_discard(ADMIN_LIVE_GROUP, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'admin_live_error',
                'message': 'Invalid JSON payload.'
            }))
            return

        if data.get('type') != 'snapshot_request':
            return

        snapshot = await self.get_active_bus_locations()
        await self.send(text_data=json.dumps({
            'type': 'admin_live_snapshot',
            'buses': snapshot,
        }))

    async def stream_periodic_snapshots(self):
        try:
            while True:
                await asyncio.sleep(3)
                snapshot = await self.get_active_bus_locations()
                await self.send(text_data=json.dumps({
                    'type': 'admin_live_snapshot',
                    'buses': snapshot,
                }))
        except asyncio.CancelledError:
            return

    async def admin_location_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'admin_location_update',
            'bus_id': event.get('bus_id'),
            'bus_number': event.get('bus_number'),
            'route_id': event.get('route_id'),
            'driver_name': event.get('driver_name'),
            'latitude': event.get('latitude'),
            'longitude': event.get('longitude'),
            'speed': event.get('speed'),
            'timestamp': event.get('timestamp'),
        }))

    @database_sync_to_async
    def check_is_admin(self, user):
        if user.is_superuser:
            return True

        user_type = str(getattr(user, 'user_type', '')).lower()
        role = str(getattr(user, 'role', '')).lower()
        if user_type in ['admin', 'super_admin', 'school_admin'] or role in ['admin', 'super_admin', 'school_admin']:
            return True

        if hasattr(user, 'staff_profile'):
            staff_role = str(user.staff_profile.role).strip().lower()
            if staff_role in ['admin', 'principal']:
                return True

        return False

    @database_sync_to_async
    def get_active_bus_locations(self):
        routes = (
            Route.objects.select_related('vehicle__driver')
            .filter(is_active=True)
            .exclude(current_latitude__isnull=True)
            .exclude(current_longitude__isnull=True)
        )

        snapshot = []
        for route in routes:
            snapshot.append({
                'bus_id': route.vehicle.id,
                'bus_number': route.vehicle.bus_number,
                'route_id': route.id,
                'driver_name': route.vehicle.driver.name if route.vehicle.driver else 'Unassigned',
                'latitude': route.current_latitude,
                'longitude': route.current_longitude,
                'speed': route.current_speed if route.current_speed is not None else 0,
                'timestamp': route.last_updated.isoformat() if route.last_updated else timezone.now().isoformat(),
            })
        return snapshot
