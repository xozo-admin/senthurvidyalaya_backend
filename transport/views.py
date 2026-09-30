from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db import transaction # Needed for safe bulk updates
from datetime import date
from datetime import datetime
import calendar

from .models import Vehicle, Route, Stop, TransportAllocation, TransportAttendance
from .serializers import VehicleSerializer, RouteSerializer, StopSerializer, TransportAllocationSerializer, TransportAttendanceSerializer
from staff.permissions import IsAdmin, IsStaff

# Import your User models
from students.models import Student
from teachers.models import Teacher
from staff.models import NonTeachingStaff
from django.db.models import Q  # <--- ADD THIS IMPORT AT THE TOP
from .models import TransportExpenseProof
from .serializers import TransportExpenseSerializer
from django.utils import timezone
from school.models import AcademicYear

# ====================================================
# 1. VEHICLE MANAGEMENT (Create/Delete Buses)
# ====================================================
class AdminVehicleView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        vehicles = Vehicle.objects.all()
        serializer = VehicleSerializer(vehicles, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        # Admin creates bus (e.g., Bus No 12)
        serializer = VehicleSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 201, "message": "Bus Created", "data": serializer.data})
        return Response(serializer.errors, status=400)

class AdminVehicleDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def delete(self, request, pk):
        vehicle = get_object_or_404(Vehicle, pk=pk)
        vehicle.delete()
        return Response({"status": 200, "message": "Bus Deleted"})


# ====================================================
# 2. ROUTE MANAGEMENT
# ====================================================
class AdminRouteView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def _normalize_trip_stops(self, data):
        trips = data.get('trips') or {}
        normalized = []

        if trips:
            for trip_key, trip_label in [('morning', 'Morning'), ('evening', 'Evening')]:
                for stop in trips.get(trip_key, {}).get('stops', []):
                    normalized.append((trip_label, stop))
            return normalized

        return [('Morning', stop) for stop in data.get('stops', [])]

    # --- ADDED GET METHOD ---
    def get(self, request):
        """ Get Route Details: ?bus_number=12 """
        bus_num = request.query_params.get('bus_number')
        if not bus_num:
            return Response({"error": "Bus Number is required"}, 400)

        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        try:
            route = vehicle.route
            serializer = RouteSerializer(route)
            return Response({"status": 200, "data": serializer.data})
        except Route.DoesNotExist:
            return Response({"status": 404, "message": "No route assigned to this bus."})

    def post(self, request):
        """
        Create/Update Route with full stop list.
        """
        data = request.data
        bus_num = data.get('bus_number')

        if not bus_num:
            return Response({"error": "Bus Number is required"}, 400)

        try:
            vehicle = Vehicle.objects.get(bus_number=bus_num)
            morning_start = data.get('morning_start') or data.get('start') or ''
            morning_end = data.get('morning_end') or data.get('end') or ''
            evening_start = data.get('evening_start') or morning_end or data.get('end') or ''
            evening_end = data.get('evening_end') or morning_start or data.get('start') or ''
        
        # 1. Create/Update the Main Route
            route, created = Route.objects.update_or_create(
                vehicle=vehicle,
                defaults={
                    'start_location': morning_start,
                    'end_location': morning_end,
                    'morning_start_location': morning_start,
                    'morning_end_location': morning_end,
                    'evening_start_location': evening_start,
                    'evening_end_location': evening_end,
                }
            )

        # 2. Clear old stops and add new ones (to avoid duplicates)
            route.stops.all().delete()

            stops_data = self._normalize_trip_stops(data)
            created_stops = []
        
            for trip_type, stop in stops_data:
                s = Stop.objects.create(
                    route=route,
                    trip_type=trip_type,
                    stop_name=stop['name'],
                    arrival_time=stop['time'],
                    order_number=stop['order'],
                    latitude=stop.get('latitude'),  # Add this line
                    longitude=stop.get('longitude')  # Add this line
                )
                created_stops.append(f"{trip_type}: {s.stop_name}")

            return Response({
                "status": 201, 
                "message": "Route Updated Successfully", 
                "bus_number": bus_num,
                "stops": created_stops
            })

        except Vehicle.DoesNotExist:
            return Response({"error": f"Bus Number '{bus_num}' not found"}, 404)
        except Exception as e:
            return Response({"error": str(e)}, 400)

    # --- ADDED PUT METHOD ---
    def put(self, request):
        """ Update Route (Uses same logic as POST to replace/update) """
        return self.post(request)

    # --- NEW PATCH METHOD (Partial Update for Start/End Location) ---
    def patch(self, request):
        """ 
        Update ONLY Start/End Location without touching Stops.
        Body: { "bus_number": "12", "start": "New Start Name" }
        """
        bus_num = request.data.get('bus_number')
        if not bus_num: 
            return Response({"error": "Bus Number is required"}, 400)

        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        
        try:
            route = vehicle.route
            
            # Update fields ONLY if they are present in the request
            updated_fields = []
            if 'start_location' in request.data:
                route.start_location = request.data['start_location']
                route.morning_start_location = request.data['start_location']
                updated_fields.extend(['start_location', 'morning_start_location'])
            
            if 'end_location' in request.data:
                route.end_location = request.data['end_location']
                route.morning_end_location = request.data['end_location']
                updated_fields.extend(['end_location', 'morning_end_location'])

            trip_field_map = {
                'morning_start_location': 'morning_start_location',
                'morning_end_location': 'morning_end_location',
                'evening_start_location': 'evening_start_location',
                'evening_end_location': 'evening_end_location',
            }

            for request_field, model_field in trip_field_map.items():
                if request_field in request.data:
                    setattr(route, model_field, request.data[request_field])
                    updated_fields.append(model_field)
            
            if updated_fields:
                route.save()
            
            return Response({
                "status": 200, 
                "message": "Route Location Updated", 
                "start": route.start_location,
                "end": route.end_location
            })
            
        except Route.DoesNotExist:
            return Response({"error": "No route found for this bus"}, 404)

    # --- ADDED DELETE METHOD ---
    def delete(self, request):
        """ Delete Route: ?bus_number=12 """
        bus_num = request.query_params.get('bus_number')
        if not bus_num:
            return Response({"error": "Bus Number is required"}, 400)

        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        
        try:
            vehicle.route.delete() # Cascades to stops
            return Response({"status": 200, "message": f"Route deleted for Bus {bus_num}"})
        except Route.DoesNotExist:
            return Response({"error": "No route found to delete"}, 404)
# --- NEW CLASS: Granular Stop Management (Edit/Delete Single Stop) ---
class AdminStopManageView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def put(self, request):
        """ 
        Edit a Single Stop by ID.
        Body: { "stop_id": 5, "name": "New Name", "time": "08:15" } 
        """
        stop_id = request.data.get('stop_id')
        new_name = request.data.get('name')
        new_time = request.data.get('time')
        new_order = request.data.get('order')
        new_trip_type = request.data.get('trip_type')

        stop = get_object_or_404(Stop, id=stop_id)

        if new_name: stop.stop_name = new_name
        if new_time: stop.arrival_time = new_time
        if new_order: stop.order_number = new_order
        if new_trip_type in ['Morning', 'Evening']:
            stop.trip_type = new_trip_type
        
        stop.save()
        
        return Response({
            "status": 200, 
            "message": "Stop Updated", 
            "data": StopSerializer(stop).data
        })

    def delete(self, request):
        """ 
        Delete a Single Stop by ID.
        Body: { "stop_id": 5 } 
        """
        stop_id = request.data.get('stop_id')
        stop = get_object_or_404(Stop, id=stop_id)
        
        stop.delete()
        return Response({"status": 200, "message": "Stop Deleted Successfully"})


# ====================================================
# 3. BULK ALLOCATION (Assign People by ID + Strict Check)
# ====================================================
class AdminBulkAllocationView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    # --- ADDED GET METHOD ---
    def get(self, request):
        """ View Allocation IDs: ?bus_number=12 """
        bus_num = request.query_params.get('bus_number')
        if not bus_num:
            return Response({"error": "Bus Number is required"}, 400)

        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        allocations = TransportAllocation.objects.filter(vehicle=vehicle).select_related('stop', 'student', 'teacher', 'staff')

        student_details = []
        teacher_details = []
        staff_details = []
        passengers = []

        for allocation in allocations:
            stop_id = allocation.stop.id if allocation.stop else None
            stop_name = allocation.stop.stop_name if allocation.stop else None

            if allocation.user_type == 'Student' and allocation.student:
                student_details.append({
                    "id": allocation.student.student_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })
                passengers.append({
                    "type": "Student",
                    "id": allocation.student.student_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })
            elif allocation.user_type == 'Teacher' and allocation.teacher:
                teacher_details.append({
                    "id": allocation.teacher.teacher_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })
                passengers.append({
                    "type": "Teacher",
                    "id": allocation.teacher.teacher_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })
            elif allocation.user_type == 'Staff' and allocation.staff:
                staff_details.append({
                    "id": allocation.staff.staff_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })
                passengers.append({
                    "type": "Staff",
                    "id": allocation.staff.staff_id,
                    "stop_id": stop_id,
                    "stop_name": stop_name,
                })

        data = {
            "bus_number": bus_num,
            "students": [item["id"] for item in student_details],
            "teachers": [item["id"] for item in teacher_details],
            "staff": [item["id"] for item in staff_details],
            "student_details": student_details,
            "teacher_details": teacher_details,
            "staff_details": staff_details,
            "passengers": passengers,
        }
        return Response({"status": 200, "data": data})

    def post(self, request):
        """ 
        Assign Users to Bus. 
        INCLUDES STRICT CHECK: Returns error if user is already on another bus.
        INCLUDES CAPACITY CHECK: Returns error if total passengers exceed bus capacity.
        """
        data = request.data
        bus_num = data.get('bus_number')
        
        student_payload = data.get('students', [])
        teacher_payload = data.get('teachers', [])
        staff_payload = data.get('staff', [])

        try:
            vehicle = Vehicle.objects.get(bus_number=bus_num)
        except Vehicle.DoesNotExist:
            return Response({"error": f"Bus Number '{bus_num}' not found"}, 404)

        log_added = []
        log_errors = []

        def normalize_payload_items(items, user_type):
            if not isinstance(items, list):
                return []

            normalized = []
            seen_ids = set()
            for item in items:
                if isinstance(item, dict):
                    passenger_id = item.get('id') or item.get(f'{user_type.lower()}_id')
                    stop_id = item.get('stop_id')
                else:
                    passenger_id = item
                    stop_id = None

                if passenger_id is None:
                    continue

                passenger_id = str(passenger_id).strip()
                if not passenger_id or passenger_id in seen_ids:
                    continue

                seen_ids.add(passenger_id)
                normalized.append({
                    "id": passenger_id,
                    "stop_id": stop_id,
                })
            return normalized

        normalized_students = normalize_payload_items(student_payload, 'Student')
        normalized_teachers = normalize_payload_items(teacher_payload, 'Teacher')
        normalized_staff = normalize_payload_items(staff_payload, 'Staff')

        try:
            route = vehicle.route
        except Route.DoesNotExist:
            route = None

        valid_student_allocations = []
        valid_teacher_allocations = []
        valid_staff_allocations = []
        to_add_count = 0

        def resolve_stop(stop_id, passenger_label):
            if stop_id in (None, ''):
                return None

            if not route:
                log_errors.append(f"{passenger_label}: bus has no route, stop not assigned")
                return None

            try:
                stop_id_int = int(stop_id)
            except (TypeError, ValueError):
                log_errors.append(f"{passenger_label}: invalid stop id '{stop_id}'")
                return None

            stop = Stop.objects.filter(id=stop_id_int, route=route).first()
            if not stop:
                log_errors.append(f"{passenger_label}: stop id '{stop_id_int}' does not belong to Bus {bus_num}")
                return None
            return stop

        for student_item in normalized_students:
            student_id = student_item['id']
            try:
                student = Student.objects.get(student_id=student_id)
            except Student.DoesNotExist:
                log_errors.append(f"Student ID {student_id} not found")
                continue

            existing = TransportAllocation.objects.filter(student=student).first()
            if existing and existing.vehicle != vehicle:
                log_errors.append(f"Student {student_id} is already on Bus {existing.vehicle.bus_number}")
                continue

            if not existing:
                to_add_count += 1

            stop = resolve_stop(student_item.get('stop_id'), f"Student {student_id}")
            valid_student_allocations.append((student, stop))

        for teacher_item in normalized_teachers:
            teacher_id = teacher_item['id']
            try:
                teacher = Teacher.objects.get(teacher_id=teacher_id)
            except Teacher.DoesNotExist:
                log_errors.append(f"Teacher ID {teacher_id} not found")
                continue

            existing = TransportAllocation.objects.filter(teacher=teacher).first()
            if existing and existing.vehicle != vehicle:
                log_errors.append(f"Teacher {teacher_id} is already on Bus {existing.vehicle.bus_number}")
                continue

            if not existing:
                to_add_count += 1

            stop = resolve_stop(teacher_item.get('stop_id'), f"Teacher {teacher_id}")
            valid_teacher_allocations.append((teacher, stop))

        for staff_item in normalized_staff:
            staff_id = staff_item['id']
            try:
                staff_member = NonTeachingStaff.objects.get(staff_id=staff_id)
            except NonTeachingStaff.DoesNotExist:
                log_errors.append(f"Staff ID {staff_id} not found")
                continue

            existing = TransportAllocation.objects.filter(staff=staff_member).first()
            if existing and existing.vehicle != vehicle:
                log_errors.append(f"Staff {staff_id} is already on Bus {existing.vehicle.bus_number}")
                continue

            if not existing:
                to_add_count += 1

            stop = resolve_stop(staff_item.get('stop_id'), f"Staff {staff_id}")
            valid_staff_allocations.append((staff_member, stop))

        current_count = TransportAllocation.objects.filter(vehicle=vehicle).count()
        total_projected = current_count + to_add_count

        if total_projected > vehicle.capacity:
            return Response({
                "error": "Capacity Exceeded",
                "message": f"Bus {bus_num} has a capacity of {vehicle.capacity}. "
                           f"Currently occupied: {current_count}. "
                           f"You are trying to add {to_add_count} new people, which would total {total_projected}. "
                           f"Available seats: {vehicle.capacity - current_count}."
            }, 400)

        with transaction.atomic():
            for student, stop in valid_student_allocations:
                TransportAllocation.objects.update_or_create(
                    student=student,
                    defaults={'vehicle': vehicle, 'user_type': 'Student', 'stop': stop}
                )
                log_added.append(f"Student {student.student_id}")

            for teacher, stop in valid_teacher_allocations:
                TransportAllocation.objects.update_or_create(
                    teacher=teacher,
                    defaults={'vehicle': vehicle, 'user_type': 'Teacher', 'stop': stop}
                )
                log_added.append(f"Teacher {teacher.teacher_id}")

            for staff_member, stop in valid_staff_allocations:
                TransportAllocation.objects.update_or_create(
                    staff=staff_member,
                    defaults={'vehicle': vehicle, 'user_type': 'Staff', 'stop': stop}
                )
                log_added.append(f"Staff {staff_member.staff_id}")

        return Response({
            "status": 200,
            "message": "Allocation Processed",
            "bus_number": bus_num,
            "added": log_added,
            "errors": log_errors
        })

    # --- ADDED DELETE METHOD ---
    def delete(self, request):
        """ 
        Delete specific passengers from a bus.
        Body: { "bus_number": "12", "students": ["ST01"], "teachers": [] }
        """
        data = request.data
        bus_num = data.get('bus_number')
        
        student_ids = data.get('students', [])
        teacher_ids = data.get('teachers', [])
        staff_ids = data.get('staff', [])

        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        
        deleted_count = 0
        with transaction.atomic():
            if student_ids:
                deleted_count += TransportAllocation.objects.filter(
                    vehicle=vehicle, student__student_id__in=student_ids
                ).delete()[0]
            
            if teacher_ids:
                deleted_count += TransportAllocation.objects.filter(
                    vehicle=vehicle, teacher__teacher_id__in=teacher_ids
                ).delete()[0]
                
            if staff_ids:
                deleted_count += TransportAllocation.objects.filter(
                    vehicle=vehicle, staff__staff_id__in=staff_ids
                ).delete()[0]

        return Response({"status": 200, "message": f"Removed {deleted_count} passengers from Bus {bus_num}"})


# ====================================================
# 4. VIEW PASSENGER LIST (Admin View)
# ====================================================
class AdminPassengerListView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """
        Get passengers for a bus.
        Usage: /api/transport/admin/passengers/?bus_number=12
        """
        bus_num = request.query_params.get('bus_number')
        if not bus_num:
            return Response({"error": "Param 'bus_number' is required"}, 400)

        try:
            vehicle = Vehicle.objects.get(bus_number=bus_num)
        except Vehicle.DoesNotExist:
            return Response({"error": "Bus not found"}, 404)

        allocations = TransportAllocation.objects.filter(vehicle=vehicle).select_related(
            'student', 'teacher', 'staff', 'stop'
        )

        type_order = {'Student': 0, 'Teacher': 1, 'Staff': 2}
        passenger_list = []

        for alloc in allocations:
            stop_id = alloc.stop.id if alloc.stop else None
            stop_name = alloc.stop.stop_name if alloc.stop else None

            if alloc.student:
                passenger_type = 'Student'
                passenger_id = alloc.student.student_id
                passenger_name = (
                    alloc.student.student_name
                    or getattr(alloc.student, 'name', None)
                    or passenger_id
                )
            elif alloc.teacher:
                passenger_type = 'Teacher'
                passenger_id = alloc.teacher.teacher_id
                passenger_name = (
                    alloc.teacher.name
                    or getattr(alloc.teacher, 'teacher_name', None)
                    or passenger_id
                )
            elif alloc.staff:
                passenger_type = 'Staff'
                passenger_id = alloc.staff.staff_id
                passenger_name = alloc.staff.name or passenger_id
            else:
                # Skip broken/incomplete allocation rows instead of returning "Unknown".
                continue

            passenger_list.append({
                "allocation_id": alloc.id,
                "type": passenger_type,
                "id": passenger_id,
                "name": passenger_name,
                "stop_id": stop_id,
                "stop_name": stop_name,
                "allocated_at": alloc.allocated_at,
            })

        passenger_list.sort(
            key=lambda p: (type_order.get(p.get('type', ''), 99), str(p.get('id', '')))
        )

        return Response({
            "status": 200,
            "bus_number": bus_num,
            "capacity": vehicle.capacity,
            "occupied": len(passenger_list),
            "passengers": passenger_list
        })


# ====================================================
# 5. DRIVER ASSIGNMENT (Admin Assign/Unassign Driver)
# ====================================================
class AdminAssignDriverView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    # --- ADDED GET METHOD ---
    def get(self, request):
        """ Get Current Driver: ?bus_number=12 """
        bus_num = request.query_params.get('bus_number')
        vehicle = get_object_or_404(Vehicle, bus_number=bus_num)

        if vehicle.driver:
            return Response({
                "status": 200,
                "bus_number": bus_num,
                "driver_name": vehicle.driver.name,
                "driver_id": vehicle.driver.staff_id
            })
        return Response({"status": 200, "message": "No driver assigned", "bus_number": bus_num})

    def post(self, request):
        bus_num = request.data.get('bus_number')
        staff_id = request.data.get('staff_id')

        if not bus_num or not staff_id:
            return Response({"error": "Both bus_number and staff_id are required"}, 400)

        # 1. Find the Bus
        try:
            target_vehicle = Vehicle.objects.get(bus_number=bus_num)
        except Vehicle.DoesNotExist:
            return Response({"error": f"Bus {bus_num} not found"}, 404)

        # 2. Find the Staff Member
        try:
            staff_member = NonTeachingStaff.objects.get(staff_id=staff_id)
        except NonTeachingStaff.DoesNotExist:
            return Response({"error": f"Staff ID {staff_id} not found"}, 404)

        # 3. VALIDATION: Check Role
        allowed_roles = ['transport_staff'] 
        if staff_member.role not in allowed_roles:
             return Response({
                 "error": "Invalid Role",
                 "message": f"Staff is a '{staff_member.role}'. Only 'Transport Staff' can be assigned."
             }, 400)

        # 4. VALIDATION: Check if Staff is Already Assigned to ANOTHER Bus
        # (This prevents cloning the driver)
        existing_assignment = Vehicle.objects.filter(driver=staff_member).first()
        if existing_assignment and existing_assignment.bus_number != target_vehicle.bus_number:
            return Response({
                "error": "Staff Unavailable",
                "message": f"Staff is already assigned to Bus {existing_assignment.bus_number}."
            }, 400)

        # 5. NEW VALIDATION: Check if BUS Already Has a Driver
        # (This prevents kicking out the current driver)
        if target_vehicle.driver and target_vehicle.driver != staff_member:
            return Response({
                "error": "Bus Unavailable",
                "message": f"Already {target_vehicle.driver.name} is assigned to Bus {target_vehicle.bus_number}. Please unassign them first."
            }, 400)

        # 6. Success - Assign and Save
        target_vehicle.driver = staff_member
        target_vehicle.save()

        return Response({
            "status": 200,
            "message": "Transport Staff Assigned Successfully",
            "bus_number": target_vehicle.bus_number,
            "staff_name": staff_member.name, 
            "staff_id": staff_member.staff_id
        })

    # --- ADDED DELETE METHOD ---
    def delete(self, request):
        """ Unassign Driver: ?bus_number=12 """
        bus_num = request.query_params.get('bus_number')
        target_vehicle = get_object_or_404(Vehicle, bus_number=bus_num)
        
        if target_vehicle.driver:
            name = target_vehicle.driver.name
            target_vehicle.driver = None
            target_vehicle.save()
            return Response({"status": 200, "message": f"Driver {name} unassigned from Bus {bus_num}"})
        
        return Response({"message": "No driver was assigned to this bus."}, 200)


# ====================================================
# 6. DRIVER VIEW (My Passengers Only)
# ====================================================
class DriverMyPassengersView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    def get(self, request):
        user = request.user
        
        # 1. Identify Driver
        allowed_roles = ['Transport Staff', 'Driver', 'Transport', 'transport_staff']
        
        if not hasattr(user, 'staff_profile') or user.staff_profile.role not in allowed_roles:
            return Response({"error": "Access Denied. Drivers only."}, 403)
        
        driver_profile = user.staff_profile

        # 2. Find Bus Assigned to this Driver
        try:
            my_bus = driver_profile.assigned_bus 
        except Exception:
            my_bus = Vehicle.objects.filter(driver=driver_profile).first()
        
        if not my_bus:
            return Response({"error": "No bus is currently assigned to you."}, 404)

        # 3. GET ROUTE ID (The Key Fix)
        # We need this ID so the frontend knows which WebSocket channel to connect to.
        try:
            route_id = my_bus.route.id
        except Route.DoesNotExist:
            route_id = None

        # 4. Get Passengers
        allocations = TransportAllocation.objects.filter(vehicle=my_bus).select_related('student', 'teacher', 'staff', 'stop')
        
        passenger_list = []
        for alloc in allocations:
            p_data = {
                "type": alloc.user_type,
                "name": "Unknown",
                "id": "Unknown",
                "stop_id": alloc.stop.id if alloc.stop else None,
                "stop_name": alloc.stop.stop_name if alloc.stop else None,
            }
            
            if alloc.user_type == 'Student' and alloc.student:
                p_data['name'] = alloc.student.student_name
                p_data['id'] = alloc.student.student_id 

            elif alloc.user_type == 'Teacher' and alloc.teacher:
                p_data['name'] = alloc.teacher.name
                p_data['id'] = alloc.teacher.teacher_id

            elif alloc.user_type == 'Staff' and alloc.staff:
                p_data['name'] = alloc.staff.name
                p_data['id'] = alloc.staff.staff_id
            
            passenger_list.append(p_data)

        return Response({
            "status": 200,
            "my_bus": my_bus.bus_number,
            "route_id": route_id,             # <--- ADDED THIS FOR WEBSOCKET
            "total_passengers": len(passenger_list),
            "passengers": passenger_list
        })
        
# ====================================================
# 7. TRACKING API (Student/Staff View - My Bus Info)
# ====================================================

class MyBusInfoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        allocation = None
        
        # 1. Find allocation based on user type
        if hasattr(user, 'student_profile'):
            allocation = TransportAllocation.objects.filter(student=user.student_profile).first()
        elif hasattr(user, 'staff_profile'):
            allocation = TransportAllocation.objects.filter(staff=user.staff_profile).first()
        elif hasattr(user, 'teacher_profile'):
            allocation = TransportAllocation.objects.filter(teacher=user.teacher_profile).first()
        
        if not allocation:
            return Response({"error": "No bus assigned to you."}, 404)

        vehicle = allocation.vehicle
        
        # 2. Get Route and Route ID safely
        try:
            route = vehicle.route
            route_id = route.id  # <--- Get the ID
        except Route.DoesNotExist:
            route = None
            route_id = None

        return Response({
            "status": 200,
            "bus_number": vehicle.bus_number,
            "reg_number": vehicle.registration_number,
            "route_id": route_id,             # <--- ADDED THIS FOR WEBSOCKET
            "driver_name": vehicle.driver.name if vehicle.driver else "Not Assigned",
            "route_start": route.start_location if route else "N/A",
            "route_end": route.end_location if route else "N/A",
            "morning_start_location": route.morning_start_location if route else "",
            "morning_end_location": route.morning_end_location if route else "",
            "evening_start_location": route.evening_start_location if route else "",
            "evening_end_location": route.evening_end_location if route else "",
            "stops": StopSerializer(route.stops.all().order_by('trip_type', 'order_number'), many=True).data if route else []
        })


class BusAttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get_driver_bus(self, user):
        """Helper to find the bus assigned to the logged-in driver."""
        if not hasattr(user, 'staff_profile'):
            return None
        try:
            return user.staff_profile.assigned_bus
        except:
            return Vehicle.objects.filter(driver=user.staff_profile).first()

    def get(self, request):
        """
        FETCH PASSENGERS & STATUS
        Constraint: Can only view dates within the CURRENT YEAR.
        Constraint: Cannot view FUTURE dates.
        Constraint: Filter out passengers who joined AFTER the requested date.
        """
        my_bus = self.get_driver_bus(request.user)
        if not my_bus:
            return Response({"error": "You are not assigned to any bus."}, 403)

        query_date_str = request.query_params.get('date', str(date.today()))
        trip_param = request.query_params.get('trip') 

        # --- VALIDATION: DATE FORMAT ---
        try:
            query_date_obj = datetime.strptime(query_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, 400)

        today = date.today()
        
        # 1. Year Check
        if query_date_obj.year != today.year:
            return Response({
                "error": "Restricted Access", 
                "message": f"You can only view attendance records for the current year ({today.year})."
            }, 400)

        # 2. Future Date Check
        if query_date_obj > today:
            return Response({
                "error": "Invalid Date",
                "message": "Cannot view attendance records for future dates."
            }, 400)

        # Determine default status
        default_status = "Pending" if query_date_obj == today else "Not Marked"

        # Optimized Query
        allocations = TransportAllocation.objects.filter(vehicle=my_bus).select_related('student', 'teacher', 'staff')

        response_data = []
        for alloc in allocations:
            # ====================================================
            # NEW VALIDATION: ALLOCATION DATE CHECK
            # ====================================================
            # If the passenger joined the bus AFTER the date we are looking at, skip them.
            if alloc.allocated_at and alloc.allocated_at > query_date_obj:
                continue

            # Fetch Name & ID
            p_name = "Unknown"
            p_id = "Unknown"
            
            if alloc.user_type == 'Student' and alloc.student:
                p_name = alloc.student.student_name
                p_id = alloc.student.student_id
            elif alloc.user_type == 'Teacher' and alloc.teacher:
                p_name = alloc.teacher.name
                p_id = alloc.teacher.teacher_id
            elif alloc.user_type == 'Staff' and alloc.staff:
                p_name = alloc.staff.name
                p_id = alloc.staff.staff_id

            # Base Data
            passenger_data = {
                "passenger_id": p_id,
                "passenger_name": p_name,
                "user_type": alloc.user_type,
            }

            # LOGIC: Show One Trip vs Both Trips
            if trip_param:
                # Specific Trip Requested
                record = TransportAttendance.objects.filter(
                    allocation=alloc, date=query_date_str, trip_type=trip_param
                ).first()
                
                passenger_data["status"] = record.status if record else default_status
                passenger_data["is_marked"] = record is not None
            
            else:
                # No Trip Specified -> Show BOTH
                morning_rec = TransportAttendance.objects.filter(
                    allocation=alloc, date=query_date_str, trip_type='Morning'
                ).first()
                
                evening_rec = TransportAttendance.objects.filter(
                    allocation=alloc, date=query_date_str, trip_type='Evening'
                ).first()

                passenger_data["morning_status"] = morning_rec.status if morning_rec else default_status
                passenger_data["evening_status"] = evening_rec.status if evening_rec else default_status

            response_data.append(passenger_data)

        return Response({
            "bus_id": my_bus.id,
            "bus_number": my_bus.bus_number,
            "date": query_date_str,
            "trip": trip_param if trip_param else "All",
            "passengers": response_data
        })


    def post(self, request):
        """
        MARK ATTENDANCE
        Constraint: Can ONLY mark for TODAY. Past/Future dates are blocked.
        """
        my_bus = self.get_driver_bus(request.user)
        if not my_bus:
            return Response({"error": "Access Denied. You are not a driver."}, 403)

        data = request.data
        target_date_str = data.get('date', str(date.today()))
        trip_type = data.get('trip', 'Morning')
        attendance_list = data.get('attendance_list', [])

        # --- VALIDATION: STRICTLY TODAY ONLY ---
        try:
            target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format."}, 400)

        today = date.today()

        if target_date_obj != today:
             return Response({
                 "error": "Action Forbidden",
                 "message": f"You can only mark attendance for TODAY ({today}). You cannot mark past or future dates."
             }, 400)

        # --- Proceed with Saving ---
        saved_count = 0
        errors = []

        for entry in attendance_list:
            p_id = entry.get('passenger_id') 
            status = entry.get('status')

            if not p_id: continue

            # Smart Search for ID on this bus
            allocation = TransportAllocation.objects.filter(vehicle=my_bus).filter(
                Q(student__student_id=p_id) | 
                Q(teacher__teacher_id=p_id) | 
                Q(staff__staff_id=p_id)
            ).first()

            if allocation:
                # Found valid passenger -> Save Attendance
                TransportAttendance.objects.update_or_create(
                    allocation=allocation,
                    date=target_date_str,
                    trip_type=trip_type,
                    defaults={
                        'status': status,
                        'marked_by': request.user.staff_profile
                    }
                )
                saved_count += 1
            else:
                errors.append(f"User ID '{p_id}' is not assigned to Bus {my_bus.bus_number}")

        return Response({
            "message": "Attendance Processed",
            "date": target_date_str,
            "trip": trip_type,
            "success_count": saved_count,
            "errors": errors 
        })


class UserBusAttendanceHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get_user_allocation(self, user):
        """ Helper to find the user's seat (Allocation) on the bus. """
        if hasattr(user, 'student_profile'):
            return TransportAllocation.objects.filter(student=user.student_profile).first()
        elif hasattr(user, 'teacher_profile'):
            return TransportAllocation.objects.filter(teacher=user.teacher_profile).first()
        elif hasattr(user, 'staff_profile'):
            return TransportAllocation.objects.filter(staff=user.staff_profile).first()
        return None

    def get_driver_name(self, record):
        """ Helper to safely get driver name if record exists """
        if record and record.marked_by:
            return record.marked_by.name
        return "N/A"

    def get_status_label(self, record, target_date, today):
        """
        Helper logic: 
        - If record exists -> Show Status (Present/Absent)
        - If NO record and date is TODAY -> Show "Pending"
        - If NO record and date is PAST -> Show "Not Marked"
        """
        if record:
            return record.status
        
        if target_date == today:
            return "Pending"
        
        return "Not Marked"

    def get(self, request):
        # 1. Identify User & Allocation
        allocation = self.get_user_allocation(request.user)
        if not allocation:
            return Response({"error": "You are not assigned to any bus transport."}, 404)

        # 2. Get Allocation Date (Strict: No Fallback)
        allocated_at = allocation.allocated_at
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 3. Check Parameters
        date_param = request.query_params.get('date')
        month_param = request.query_params.get('month')
        
        today = date.today()

        # --- SCENARIO 1: SINGLE DATE VIEW ---
        if date_param:
            try:
                target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, 400)

            # Academic Year Check
            if target_date < active_year.start_date or target_date > active_year.end_date:
                 return Response({
                     "error": "Restricted Access",
                     "message": f"Active academic year only ({active_year.name})."
                 }, 400)

            # Future Date Check
            if target_date > today:
                 return Response({"error": "Invalid Date", "message": "Cannot view future dates."}, 400)

            # --- STRICT VALIDATION: JOINING DATE CHECK ---
            # If allocated_at is None, this line will CRASH (500 Error), as requested.
            if target_date < allocated_at:
                return Response({
                    "message": f"You have joined bus on {allocated_at}. You have no records for past.",
                    "allocated_at": allocated_at
                }, 200)

            # Fetch Records
            morning_rec = TransportAttendance.objects.filter(
                allocation=allocation, date=target_date, trip_type='Morning'
            ).first()
            
            evening_rec = TransportAttendance.objects.filter(
                allocation=allocation, date=target_date, trip_type='Evening'
            ).first()

            return Response({
                "date": date_param,
                "bus_number": allocation.vehicle.bus_number,
                "history": {
                    "morning": {
                        "status": self.get_status_label(morning_rec, target_date, today),
                        "marked_by": self.get_driver_name(morning_rec)
                    },
                    "evening": {
                        "status": self.get_status_label(evening_rec, target_date, today),
                        "marked_by": self.get_driver_name(evening_rec)
                    }
                }
            })

        # --- SCENARIO 2: MONTH VIEW ---
        elif month_param:
            try:
                month = int(month_param)
                if month < 1 or month > 12: raise ValueError
            except ValueError:
                return Response({"error": "Invalid month. Use 1-12."}, 400)

            start_month = active_year.start_date.month
            start_year = active_year.start_date.year
            end_year = active_year.end_date.year
            target_year = start_year if month >= start_month else end_year

            first_day_of_month = date(target_year, month, 1)
            last_day_of_month = date(target_year, month, calendar.monthrange(target_year, month)[1])

            if last_day_of_month < active_year.start_date or first_day_of_month > active_year.end_date:
                return Response({
                    "error": "Restricted Access",
                    "message": f"Active academic year only ({active_year.name}).",
                    "history": []
                }, 400)

            # Future Month Check (relative to today)
            if first_day_of_month > today:
                return Response({"message": "No history for future months.", "history": []})

            num_days = calendar.monthrange(target_year, month)[1]

            # --- STRICT VALIDATION: PRE-JOINING MONTH CHECK ---
            # Check if the requested month is entirely before the joining date
            # We construct the last day of the requested month to compare.
            last_day_of_requested_month = date(target_year, month, num_days)
            
            if last_day_of_requested_month < allocated_at:
                return Response({
                    "message": f"You have joined bus on {allocated_at}. You have no records for past.",
                    "allocated_at": allocated_at,
                    "history": []
                }, 200)

            month_records = TransportAttendance.objects.filter(
                allocation=allocation,
                date__year=target_year,
                date__month=month
            )
            
            record_map = {}
            for rec in month_records:
                day = rec.date.day
                record_map[(day, rec.trip_type)] = rec

            history_list = []
            
            # Decide the limit day (Today vs End of Month)
            limit_day = num_days
            if month == today.month and target_year == today.year:
                limit_day = today.day 

            for d in range(1, limit_day + 1):
                # Construct date object for this specific day
                loop_date = date(target_year, month, d)

                # --- NEW FILTER: Hide days before joining ---
                if loop_date < allocated_at:
                    continue

                morning = record_map.get((d, 'Morning'))
                evening = record_map.get((d, 'Evening'))

                history_list.append({
                    "day": d,
                    "date": str(loop_date),
                    "morning": {
                        "status": self.get_status_label(morning, loop_date, today),
                        "marked_by": self.get_driver_name(morning)
                    },
                    "evening": {
                        "status": self.get_status_label(evening, loop_date, today),
                        "marked_by": self.get_driver_name(evening)
                    }
                })

            return Response({
                "month": month,
                "year": target_year,
                "bus_number": allocation.vehicle.bus_number,
                "allocated_at": allocated_at,
                "history": history_list
            })

        else:
            return Response({"error": "Please provide either ?date=YYYY-MM-DD or ?month=MM parameter."}, 400)

# ====================================================
# 8. DRIVER EXPENSE UPLOAD (Upload/View/Edit Proofs)
# ====================================================
class DriverExpenseView(APIView):
    permission_classes = [IsAuthenticated]

    def get_driver_bus(self, user):
        """ Helper to find the bus assigned to the logged-in driver. """
        if not hasattr(user, 'staff_profile'): return None
        try:
            return user.staff_profile.assigned_bus
        except:
            return Vehicle.objects.filter(driver=user.staff_profile).first()

    def get(self, request):
        """ 
        View My Uploads 
        Filters Enabled: ?date=YYYY-MM-DD or ?month=MM 
        """
        my_bus = self.get_driver_bus(request.user)
        if not my_bus:
            return Response({"error": "Access Denied. No bus assigned."}, 403)

        # Show only proofs uploaded by THIS driver for THIS bus
        expenses = TransportExpenseProof.objects.filter(uploader=request.user.staff_profile).order_by('-timestamp')

        # --- ADDED FILTERS FOR DRIVER TOO ---
        date_param = request.query_params.get('date')
        month_param = request.query_params.get('month')
        year_param = request.query_params.get('year')

        if date_param:
            expenses = expenses.filter(timestamp__date=date_param)
        
        elif month_param:
            try:
                target_year = int(year_param) if year_param else datetime.now().year
                target_month = int(month_param)
                expenses = expenses.filter(timestamp__year=target_year, timestamp__month=target_month)
            except ValueError:
                return Response({"error": "Invalid Month/Year format"}, 400)
        # ------------------------------------

        serializer = TransportExpenseSerializer(expenses, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """ Upload New Proof (Bill) """
        my_bus = self.get_driver_bus(request.user)
        if not my_bus:
            return Response({"error": "Access Denied. No bus assigned."}, 403)

        # 1. Validate Bus Number Match
        input_bus_number = request.data.get('bus_number')
        if not input_bus_number:
            return Response({"error": "Bus number is required"}, 400)

        if input_bus_number != my_bus.bus_number:
            return Response({
                "error": "Bus Mismatch", 
                "message": f"You are assigned to Bus {my_bus.bus_number}, but you entered Bus {input_bus_number}. You can only upload for your assigned bus."
            }, 400)

        # 2. Check File (Mandatory)
        if 'proof_file' not in request.FILES:
             return Response({"error": "Proof file (Image/PDF) is mandatory."}, 400)

        # 3. Create Record
        serializer = TransportExpenseSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(uploader=request.user.staff_profile, vehicle=my_bus)
            return Response({"status": 201, "message": "Proof Uploaded Successfully", "data": serializer.data})
        
        return Response(serializer.errors, status=400)

    def put(self, request):
        """ Edit Title/Description/File of an existing proof """
        expense_id = request.data.get('expense_id')
        if not expense_id:
            return Response({"error": "expense_id is required to edit"}, 400)

        # Find the proof (Ensure it belongs to this driver)
        expense = get_object_or_404(TransportExpenseProof, id=expense_id, uploader=request.user.staff_profile)

        # Partial update allow (title, description, file)
        serializer = TransportExpenseSerializer(expense, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 200, "message": "Proof Updated Successfully", "data": serializer.data})
        
        return Response(serializer.errors, status=400)

# ====================================================
# 9. ADMIN EXPENSE VIEW (View All Proofs with Filters)
# ====================================================
class AdminExpenseView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """
        View Proofs with Filters:
        1. ?date=YYYY-MM-DD
        2. ?month=MM (& optional ?year=YYYY)
        """
        expenses = TransportExpenseProof.objects.all().order_by('-timestamp')

        # Filter 1: Specific Date
        date_param = request.query_params.get('date')
        if date_param:
            expenses = expenses.filter(timestamp__date=date_param)
        
        # Filter 2: Month & Year
        month_param = request.query_params.get('month')
        year_param = request.query_params.get('year')

        if month_param:
            try:
                target_year = int(year_param) if year_param else datetime.now().year
                target_month = int(month_param)
                
                expenses = expenses.filter(timestamp__year=target_year, timestamp__month=target_month)
            except ValueError:
                return Response({"error": "Invalid Month/Year format"}, 400)

        serializer = TransportExpenseSerializer(expenses, many=True)
        return Response({
            "status": 200, 
            "count": expenses.count(),
            "data": serializer.data
        })

# ====================================================
# 10. ADMIN USER HISTORY (View Full Year History)
# ====================================================
class AdminUserHistoryView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_profile_by_id(self, user_id):
        """ Helper: Search for ID in Student -> Teacher -> Staff tables """
        try:
            return Student.objects.get(student_id=user_id), 'Student'
        except Student.DoesNotExist:
            pass
        
        try:
            return Teacher.objects.get(teacher_id=user_id), 'Teacher'
        except Teacher.DoesNotExist:
            pass

        try:
            return NonTeachingStaff.objects.get(staff_id=user_id), 'Staff'
        except NonTeachingStaff.DoesNotExist:
            return None, None

    def get_status_label(self, record, target_date, today):
        if record: return record.status
        if target_date == today: return "Pending"
        return "Not Marked"

    def get(self, request):
        """
        Admin View: Full Year History for a Specific User ID
        Params: ?id=1001 & ?year=2025 (Year is optional, defaults to current)
        """
        user_id = request.query_params.get('id')
        year_param = request.query_params.get('year')

        if not user_id:
            return Response({"error": "User ID is required parameter 'id'."}, 400)

        # 1. Find User Profile
        profile, user_type = self.get_profile_by_id(user_id)
        if not profile:
            return Response({"error": f"No user found with ID '{user_id}'"}, 404)

        # 2. Find Allocation
        allocation = None
        if user_type == 'Student':
            allocation = TransportAllocation.objects.filter(student=profile).first()
        elif user_type == 'Teacher':
            allocation = TransportAllocation.objects.filter(teacher=profile).first()
        elif user_type == 'Staff':
            allocation = TransportAllocation.objects.filter(staff=profile).first()

        if not allocation:
            return Response({
                "message": f"User {user_id} ({user_type}) is not allocated to any bus.",
                "history": []
            }, 200)

        # 3. Get Allocation Date & Validate Year
        allocated_at = allocation.allocated_at
        if not allocated_at: allocated_at = date(2000, 1, 1)

        today = date.today()
        target_year = int(year_param) if year_param else today.year

        # Check if requested year is completely before joining
        if target_year < allocated_at.year:
            return Response({
                "message": f"User joined on {allocated_at}. No records exist for {target_year}.",
                "allocated_at": allocated_at,
                "year": target_year,
                "history": []
            })

        # 4. Fetch ALL records for that year in one go (Optimized)
        year_records = TransportAttendance.objects.filter(
            allocation=allocation,
            date__year=target_year
        )
        
        # Convert to Map for fast lookup: {(month, day, trip): status}
        record_map = {}
        for rec in year_records:
            record_map[(rec.date.month, rec.date.day, rec.trip_type)] = rec.status

        # 5. Build Month-by-Month Data
        full_year_data = []

        # Iterate Months 1 to 12
        for month in range(1, 13):
            # Skip future months in current year
            if target_year == today.year and month > today.month:
                break
            
            # Skip past months before allocation in joining year
            if target_year == allocated_at.year and month < allocated_at.month:
                continue

            num_days = calendar.monthrange(target_year, month)[1]
            month_data = []
            
            # Determine loop range for days
            start_day = 1
            end_day = num_days

            # If it's the joining month, start from joining day
            if target_year == allocated_at.year and month == allocated_at.month:
                start_day = allocated_at.day
            
            # If it's current month, stop at today
            if target_year == today.year and month == today.month:
                end_day = today.day

            for day in range(start_day, end_day + 1):
                loop_date = date(target_year, month, day)

                # Get Status from Map (or Default)
                m_status = record_map.get((month, day, 'Morning'))
                e_status = record_map.get((month, day, 'Evening'))
                
                # Logic: If map has value -> Use it. Else -> Pending/Not Marked
                final_m = m_status if m_status else ("Pending" if loop_date == today else "Not Marked")
                final_e = e_status if e_status else ("Pending" if loop_date == today else "Not Marked")

                month_data.append({
                    "date": str(loop_date),
                    "morning_status": final_m,
                    "evening_status": final_e
                })

            if month_data: # Only add month if it has data
                full_year_data.append({
                    "month": month,
                    "month_name": calendar.month_name[month],
                    "dates": month_data
                })

        return Response({
            "status": 200,
            "user_id": user_id,
            "user_type": user_type,
            "bus_number": allocation.vehicle.bus_number,
            "allocated_at": allocated_at,
            "year": target_year,
            "history": full_year_data
        })


# for specific date
class AdminBusDateView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_status_label(self, record, target_date, today):
        """ Helper: Determines Pending vs Not Marked vs Status """
        if record:
            return record.status
        
        if target_date == today:
            return "Pending"
        
        # If date is in the past and no record exists
        if target_date < today:
            return "Not Marked"
            
        return "Pending" # Default for future dates

    def get(self, request):
        """ 
        Admin View: Passengers on a Bus for a specific Date
        Params: ?bus_number=12 & ?date=2026-02-15
        """
        bus_num = request.query_params.get('bus_number')
        date_param = request.query_params.get('date')

        if not bus_num or not date_param:
            return Response({"error": "Both 'bus_number' and 'date' parameters are required."}, 400)

        # 1. Validate Date
        try:
            target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, 400)

        today = date.today()

        # 2. Get the Bus
        try:
            vehicle = Vehicle.objects.get(bus_number=bus_num)
        except Vehicle.DoesNotExist:
            return Response({"error": f"Bus {bus_num} not found"}, 404)

        # 3. Get All Allocations for this Bus
        allocations = TransportAllocation.objects.filter(vehicle=vehicle).select_related('student', 'teacher', 'staff')

        passenger_list = []

        # 4. Loop & Filter
        for alloc in allocations:
            # --- CRITICAL CHECK: Allocation Date ---
            # If the user wasn't allocated yet on this target_date, SKIP them.
            # (e.g. Joined March 1st, checking Feb 28th -> Skip)
            if alloc.allocated_at and alloc.allocated_at > target_date:
                continue

            # Fetch Name & ID
            p_name = "Unknown"
            p_id = "Unknown"
            
            if alloc.user_type == 'Student' and alloc.student:
                p_name = alloc.student.student_name
                p_id = alloc.student.student_id
            elif alloc.user_type == 'Teacher' and alloc.teacher:
                p_name = alloc.teacher.name
                p_id = alloc.teacher.teacher_id
            elif alloc.user_type == 'Staff' and alloc.staff:
                p_name = alloc.staff.name
                p_id = alloc.staff.staff_id

            # Fetch Attendance Records for this specific person on this specific day
            morning_rec = TransportAttendance.objects.filter(
                allocation=alloc, date=target_date, trip_type='Morning'
            ).first()
            
            evening_rec = TransportAttendance.objects.filter(
                allocation=alloc, date=target_date, trip_type='Evening'
            ).first()

            passenger_list.append({
                "passenger_id": p_id,
                "passenger_name": p_name,
                "user_type": alloc.user_type,
                "morning_status": self.get_status_label(morning_rec, target_date, today),
                "evening_status": self.get_status_label(evening_rec, target_date, today)
            })

        return Response({
            "bus_id": vehicle.id,
            "bus_number": bus_num,
            "date": date_param,
            "count": len(passenger_list),
            "passengers": passenger_list
        })



class AdminBusListView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """
        Returns a list of all buses for Admin.
        Fields: Bus Number, Registration Number, Driver ID, Driver Name.
        """
        # Fetch all buses and join with the driver table (optimization)
        vehicles = Vehicle.objects.select_related('driver').all().order_by('bus_number')

        data = []
        for v in vehicles:
            driver_id = None
            driver_name = "Unassigned"

            # Check if a driver is assigned to this bus
            if v.driver:
                driver_id = v.driver.staff_id
                driver_name = v.driver.name

            data.append({
                "bus_id": v.id,
                "bus_number": v.bus_number,
                "registration_number": v.registration_number,
                "driver_id": driver_id,
                "driver_name": driver_name
            })

        return Response({"status": 200, "count": len(data), "buses": data})

#admin view active buses 

class ActiveBusListView(APIView):
    permission_classes = [IsAuthenticated] # Admin or anyone authorized

    def get(self, request):
        """
        Returns ONLY buses that are currently ACTIVE (Driver Connected via WebSocket).
        """
        # Filter Logic: Get vehicles where the linked route has is_active=True
        active_vehicles = Vehicle.objects.filter(route__is_active=True).select_related('driver', 'route').order_by('bus_number')

        data = []
        for v in active_vehicles:
            driver_name = v.driver.name if v.driver else "Unassigned"
            
            data.append({
                "bus_id": v.id,
                "bus_number": v.bus_number,
                "driver_name": driver_name,
                "status": "Live",
                "current_location": {
                    "lat": v.route.current_latitude,
                    "lng": v.route.current_longitude,
                    "speed": v.route.current_speed,
                    "last_updated": v.route.last_updated
                }
            })

        return Response({"status": 200, "count": len(data), "active_buses": data})


###driver own dashboard

class DriverDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Unauthorized. Staff profile not found."}, 403)
        
        driver_profile = user.staff_profile
        today = timezone.now().date()

        # 1. Get the assigned bus
        my_bus = Vehicle.objects.filter(driver=driver_profile).first()

        if not my_bus:
            return Response({"has_bus": False, "message": "No bus assigned"}, status=200)

        # 2. Trip Attendance Status
        morning_marked = TransportAttendance.objects.filter(
            allocation__vehicle=my_bus, date=today, trip_type='Morning'
        ).exists()
        
        evening_marked = TransportAttendance.objects.filter(
            allocation__vehicle=my_bus, date=today, trip_type='Evening'
        ).exists()

        # 3. Passenger Counts (Split by Category)
        allocations = TransportAllocation.objects.filter(vehicle=my_bus)
        student_count = allocations.filter(user_type='Student').count()
        teacher_count = allocations.filter(user_type='Teacher').count()
        staff_count = allocations.filter(user_type='Staff').count()

        # 4. Expense Details
        # Count for current month
        uploads_count = TransportExpenseProof.objects.filter(
            uploader=driver_profile,
            timestamp__month=today.month,
            timestamp__year=today.year
        ).count()

        # Get the latest upload date
        last_upload = TransportExpenseProof.objects.filter(
            uploader=driver_profile
        ).order_by('-timestamp').first()
        
        last_upload_date = last_upload.timestamp.date() if last_upload else None

        # 5. Final Exact Response Structure
        return Response({
            "my_vehicle": {
                "bus_number": my_bus.bus_number,
                "reg_number": my_bus.registration_number,
                "capacity": my_bus.capacity,
                "is_live_now": getattr(my_bus.route, 'is_active', False) if hasattr(my_bus, 'route') else False
            },
            "trip_status": {
                "morning_attendance": "Marked" if morning_marked else "Pending",
                "evening_attendance": "Marked" if evening_marked else "Pending"
            },
            "passengers": {
                "total": allocations.count(),
                "students": student_count,
                "teachers": teacher_count,
                "staffs": staff_count
            },
            "expenses": {
                "uploads_this_month": uploads_count,
                "last_upload_date": last_upload_date
            }
        }, status=200)

## Driver Route View

class DriverRouteView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    def get(self, request):
        user = request.user
        
        # 1. Identify the Driver's Profile
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied. Staff profile not found."}, 403)
        
        driver_profile = user.staff_profile

        # 2. Find the Bus assigned to this Driver
        # We check both the direct 'assigned_bus' field or a filter on Vehicle
        try:
            my_bus = driver_profile.assigned_bus 
        except Exception:
            my_bus = Vehicle.objects.filter(driver=driver_profile).first()
        
        if not my_bus:
            return Response({"error": "No bus is currently assigned to you."}, 404)

        # 3. Fetch the Route and its Stops
        try:
            route = my_bus.route
            stops = route.stops.all().order_by('order_number') # Ensures stops are in correct sequence
            
            return Response({
                "status": 200,
                "bus_number": my_bus.bus_number,
                "route_id": route.id,
                "start_location": route.start_location,
                "end_location": route.end_location,
                "morning_start_location": route.morning_start_location,
                "morning_end_location": route.morning_end_location,
                "evening_start_location": route.evening_start_location,
                "evening_end_location": route.evening_end_location,
                "total_stops": stops.count(),
                "stops": StopSerializer(stops, many=True).data
            })

        except Route.DoesNotExist:
            return Response({
                "error": "No route has been created for your assigned bus yet.",
                "bus_number": my_bus.bus_number
            }, 404)
