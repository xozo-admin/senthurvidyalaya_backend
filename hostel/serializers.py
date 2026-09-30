from rest_framework import serializers

from .models import (
    HostelAllocation,
    HostelAttendance,
    HostelBed,
    HostelBlock,
    HostelIncident,
    HostelInOutLog,
    HostelRoom,
    HostelWardenAssignment,
)


class HostelBlockSerializer(serializers.ModelSerializer):
    room_count = serializers.IntegerField(source='rooms.count', read_only=True)

    class Meta:
        model = HostelBlock
        fields = ['id', 'name', 'description', 'gender_policy', 'is_active', 'created_by_name', 'room_count']


class HostelRoomSerializer(serializers.ModelSerializer):
    block_name = serializers.CharField(source='block.name', read_only=True)
    occupied_beds = serializers.SerializerMethodField()

    class Meta:
        model = HostelRoom
        fields = [
            'id',
            'block',
            'block_name',
            'room_number',
            'floor',
            'room_type',
            'capacity',
            'monthly_fee',
            'is_active',
            'created_by_name',
            'occupied_beds',
        ]

    def get_occupied_beds(self, obj):
        return obj.beds.filter(allocations__is_active=True).distinct().count()


class HostelBedSerializer(serializers.ModelSerializer):
    room_number = serializers.CharField(source='room.room_number', read_only=True)
    block_name = serializers.CharField(source='room.block.name', read_only=True)
    is_occupied = serializers.SerializerMethodField()

    class Meta:
        model = HostelBed
        fields = ['id', 'room', 'room_number', 'block_name', 'bed_number', 'is_active', 'created_by_name', 'is_occupied']

    def get_is_occupied(self, obj):
        return obj.allocations.filter(is_active=True).exists()


class HostelWardenAssignmentSerializer(serializers.ModelSerializer):
    block_name = serializers.CharField(source='block.name', read_only=True)
    staff_name = serializers.CharField(source='staff.name', read_only=True)
    staff_id = serializers.CharField(source='staff.staff_id', read_only=True)

    class Meta:
        model = HostelWardenAssignment
        fields = ['id', 'block', 'block_name', 'staff', 'staff_name', 'staff_id', 'is_primary', 'is_active', 'created_by_name']


class HostelAllocationSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.student_name', read_only=True)
    student_id = serializers.CharField(source='student.student_id', read_only=True)
    bed_number = serializers.CharField(source='bed.bed_number', read_only=True)
    room_number = serializers.CharField(source='bed.room.room_number', read_only=True)
    block_name = serializers.CharField(source='bed.room.block.name', read_only=True)
    academic_year_name = serializers.CharField(source='academic_year.name', read_only=True)

    class Meta:
        model = HostelAllocation
        fields = [
            'id',
            'student',
            'student_name',
            'student_id',
            'bed',
            'bed_number',
            'room_number',
            'block_name',
            'academic_year',
            'academic_year_name',
            'check_in_date',
            'expected_check_out_date',
            'check_out_date',
            'emergency_contact_name',
            'emergency_contact_phone',
            'notes',
            'is_active',
            'created_by_name',
            'created_at',
            'updated_at',
        ]


class HostelAttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='allocation.student.student_name', read_only=True)
    student_id = serializers.CharField(source='allocation.student.student_id', read_only=True)
    marked_by_name = serializers.CharField(source='marked_by.name', read_only=True)

    class Meta:
        model = HostelAttendance
        fields = [
            'id',
            'allocation',
            'student_name',
            'student_id',
            'date',
            'status',
            'remarks',
            'marked_by',
            'marked_by_name',
            'created_by_name',
            'created_at',
        ]


class HostelIncidentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='allocation.student.student_name', read_only=True)
    student_id = serializers.CharField(source='allocation.student.student_id', read_only=True)
    reported_by_name = serializers.CharField(source='reported_by.name', read_only=True)

    class Meta:
        model = HostelIncident
        fields = [
            'id',
            'allocation',
            'student_name',
            'student_id',
            'title',
            'description',
            'severity',
            'occurred_at',
            'reported_by',
            'reported_by_name',
            'created_by_name',
            'resolved',
            'resolution_note',
            'created_at',
        ]


class HostelInOutLogSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='allocation.student.student_name', read_only=True)
    student_id = serializers.CharField(source='allocation.student.student_id', read_only=True)
    bed_number = serializers.CharField(source='allocation.bed.bed_number', read_only=True)
    room_number = serializers.CharField(source='allocation.bed.room.room_number', read_only=True)
    block_name = serializers.CharField(source='allocation.bed.room.block.name', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.name', read_only=True)

    class Meta:
        model = HostelInOutLog
        fields = [
            'id',
            'allocation',
            'student_name',
            'student_id',
            'bed_number',
            'room_number',
            'block_name',
            'movement_type',
            'moved_at',
            'reason',
            'recorded_by',
            'recorded_by_name',
            'created_by_name',
            'created_at',
        ]
