from rest_framework import serializers
from .models import Vehicle, Route, Stop, TransportAllocation
from rest_framework import serializers
from .models import TransportExpenseProof


# 1. STOP SERIALIZER
class StopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stop
        fields = ['id', 'trip_type', 'stop_name', 'order_number', 'arrival_time', 'latitude', 'longitude']

# 2. ROUTE SERIALIZER (Includes Stops)
class RouteSerializer(serializers.ModelSerializer):
    stops = StopSerializer(many=True, read_only=True)
    class Meta:
        model = Route
        fields = [
            'id',
            'vehicle',
            'start_location',
            'end_location',
            'morning_start_location',
            'morning_end_location',
            'evening_start_location',
            'evening_end_location',
            'stops',
        ]

# 3. VEHICLE SERIALIZER
class VehicleSerializer(serializers.ModelSerializer):
    # Show route details if they exist
    route = RouteSerializer(read_only=True)
    class Meta:
        model = Vehicle
        fields = '__all__'

# 4. ALLOCATION SERIALIZER
class TransportAllocationSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source='vehicle.bus_number', read_only=True)
    
    class Meta:
        model = TransportAllocation
        fields = '__all__'


# transport/serializers.py
from rest_framework import serializers
from .models import TransportAttendance

class TransportAttendanceSerializer(serializers.ModelSerializer):
    passenger_name = serializers.SerializerMethodField()
    passenger_id = serializers.SerializerMethodField()
    passenger_type = serializers.CharField(source='allocation.user_type', read_only=True)
    allocation_id = serializers.PrimaryKeyRelatedField(read_only=True, source='allocation')

    class Meta:
        model = TransportAttendance
        fields = ['id', 'allocation_id', 'passenger_id', 'passenger_name', 'passenger_type', 'date', 'trip_type', 'status']

    def get_passenger_name(self, obj):
        # Helper to fetch name regardless of Student/Teacher/Staff
        alloc = obj.allocation
        if alloc.user_type == 'Student' and alloc.student:
            return alloc.student.student_name
        elif alloc.user_type == 'Teacher' and alloc.teacher:
            return alloc.teacher.name
        elif alloc.user_type == 'Staff' and alloc.staff:
            return alloc.staff.name
        return "Unknown"

    def get_passenger_id(self, obj):
        # Helper to fetch ID (e.g., STU001)
        alloc = obj.allocation
        if alloc.user_type == 'Student' and alloc.student:
            return alloc.student.student_id
        elif alloc.user_type == 'Teacher' and alloc.teacher:
            return alloc.teacher.teacher_id
        elif alloc.user_type == 'Staff' and alloc.staff:
            return alloc.staff.staff_id
        return "N/A"



# transport/serializers.py

class TransportExpenseSerializer(serializers.ModelSerializer):
    uploader_name = serializers.CharField(source='uploader.name', read_only=True)
    uploader_id = serializers.CharField(source='uploader.staff_id', read_only=True) # <--- ADDED THIS
    bus_number = serializers.CharField(source='vehicle.bus_number', read_only=True)
    
    class Meta:
        model = TransportExpenseProof
        fields = ['id', 'uploader_name', 'uploader_id', 'bus_number', 'title', 'description', 'proof_file', 'timestamp']
        read_only_fields = ['id', 'timestamp', 'uploader_name', 'uploader_id', 'bus_number']
