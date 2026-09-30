from django.shortcuts import get_object_or_404
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
from .permissions import IsAdminOrHostelStaff, IsAdminUserType, IsHostelStaffUserType, IsStudentUserType
from .serializers import (
    HostelAllocationSerializer,
    HostelAttendanceSerializer,
    HostelBedSerializer,
    HostelBlockSerializer,
    HostelIncidentSerializer,
    HostelInOutLogSerializer,
    HostelRoomSerializer,
    HostelWardenAssignmentSerializer,
)


def _is_hostel_warden(user):
    return bool(
        user
        and user.is_authenticated
        and user.user_type == 'staff'
        and hasattr(user, 'staff_profile')
        and getattr(user.staff_profile, 'role', None) == 'hostel_warden'
    )


def _staff_block_ids(user):
    if not hasattr(user, 'staff_profile'):
        return []
    return list(
        HostelWardenAssignment.objects.filter(staff=user.staff_profile, is_active=True).values_list('block_id', flat=True)
    )


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actor_name(user):
    if not user or not user.is_authenticated:
        return ''
    if hasattr(user, 'staff_profile') and getattr(user.staff_profile, 'name', None):
        return user.staff_profile.name
    full_name = str(getattr(user, 'full_name', '') or '').strip()
    if full_name:
        return full_name
    first_name = str(getattr(user, 'first_name', '') or '').strip()
    last_name = str(getattr(user, 'last_name', '') or '').strip()
    combined = f'{first_name} {last_name}'.strip()
    if combined:
        return combined
    return str(getattr(user, 'username', '') or '').strip()


def _flatten_error_messages(errors):
    messages = []
    if isinstance(errors, dict):
        for field, value in errors.items():
            field_label = ' '.join(str(field).split('_')).strip()
            child_messages = _flatten_error_messages(value)
            if not child_messages:
                continue
            for child in child_messages:
                if field == 'non_field_errors':
                    messages.append(child)
                else:
                    messages.append(f"{field_label}: {child}")
        return messages
    if isinstance(errors, list):
        for item in errors:
            messages.extend(_flatten_error_messages(item))
        return messages
    if errors in (None, ''):
        return messages
    return [str(errors)]


def _serializer_error_response(errors, fallback='Please check the submitted data.'):
    messages = _flatten_error_messages(errors)
    return Response(
        {
            'error': messages[0] if messages else fallback,
            'details': errors,
        },
        status=400,
    )


def _protected_delete_error_response(entity_label: str):
    return Response(
        {
            'error': f'This {entity_label} cannot be deleted because it is linked to existing records. Please remove dependent records first.'
        },
        status=400,
    )


class AdminHostelDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None

        blocks_qs = HostelBlock.objects.all()
        rooms_qs = HostelRoom.objects.all()
        beds_qs = HostelBed.objects.filter(is_active=True)
        allocations_qs = HostelAllocation.objects.filter(is_active=True)
        incidents_qs = HostelIncident.objects.filter(resolved=False)

        if block_ids is not None:
            blocks_qs = blocks_qs.filter(id__in=block_ids)
            rooms_qs = rooms_qs.filter(block_id__in=block_ids)
            beds_qs = beds_qs.filter(room__block_id__in=block_ids)
            allocations_qs = allocations_qs.filter(bed__room__block_id__in=block_ids)
            incidents_qs = incidents_qs.filter(allocation__bed__room__block_id__in=block_ids)

        total_blocks = blocks_qs.count()
        total_rooms = rooms_qs.count()
        total_beds = beds_qs.count()
        occupied_beds = allocations_qs.count()
        active_allocations = allocations_qs.count()
        available_beds = max(total_beds - occupied_beds, 0)
        pending_incidents = incidents_qs.count()

        return Response(
            {
                'status': 200,
                'data': {
                    'total_blocks': total_blocks,
                    'total_rooms': total_rooms,
                    'total_beds': total_beds,
                    'occupied_beds': occupied_beds,
                    'available_beds': available_beds,
                    'active_allocations': active_allocations,
                    'pending_incidents': pending_incidents,
                },
            }
        )


class AdminHostelBlockView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelBlock.objects.all().order_by('name')
        if _is_hostel_warden(request.user):
            queryset = queryset.filter(id__in=_staff_block_ids(request.user))
        serializer = HostelBlockSerializer(queryset, many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot create blocks.'}, status=403)
        serializer = HostelBlockSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)


class AdminHostelBlockDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def put(self, request, pk):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot edit blocks.'}, status=403)
        block = get_object_or_404(HostelBlock, pk=pk)
        serializer = HostelBlockSerializer(block, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({'status': 200, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)

    def delete(self, request, pk):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot delete blocks.'}, status=403)
        block = get_object_or_404(HostelBlock, pk=pk)
        try:
            block.delete()
        except ProtectedError:
            return _protected_delete_error_response('block')
        return Response({'status': 200, 'message': 'Block deleted'})


class AdminHostelRoomView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelRoom.objects.select_related('block').all().order_by('block__name', 'room_number')
        block_id = request.query_params.get('block_id')
        allowed_block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None

        if allowed_block_ids is not None:
            queryset = queryset.filter(block_id__in=allowed_block_ids)

        if block_id:
            parsed_block_id = _to_int(block_id)
            if parsed_block_id is None:
                return Response({'error': 'Please provide a valid block id.'}, status=400)
            if allowed_block_ids is not None and parsed_block_id not in allowed_block_ids:
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
            queryset = queryset.filter(block_id=block_id)

        serializer = HostelRoomSerializer(queryset, many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        if _is_hostel_warden(request.user):
            block_id = data.get('block')
            if not block_id:
                return Response({'error': 'Please select a block.'}, status=400)
            parsed_block_id = _to_int(block_id)
            if parsed_block_id is None:
                return Response({'error': 'Please provide a valid block id.'}, status=400)
            if parsed_block_id not in _staff_block_ids(request.user):
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        serializer = HostelRoomSerializer(data=data)
        if serializer.is_valid():
            serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)


class AdminHostelRoomDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def put(self, request, pk):
        room = get_object_or_404(HostelRoom, pk=pk)
        if _is_hostel_warden(request.user) and room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        serializer = HostelRoomSerializer(room, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({'status': 200, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)

    def delete(self, request, pk):
        room = get_object_or_404(HostelRoom, pk=pk)
        if _is_hostel_warden(request.user) and room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        try:
            room.delete()
        except ProtectedError:
            return _protected_delete_error_response('room')
        return Response({'status': 200, 'message': 'Room deleted'})


class AdminHostelBedView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelBed.objects.select_related('room', 'room__block').all().order_by('room__room_number', 'bed_number')
        room_id = request.query_params.get('room_id')
        block_id = request.query_params.get('block_id')
        available_only = (request.query_params.get('available_only') or '').lower() == 'true'
        allowed_block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None

        if allowed_block_ids is not None:
            queryset = queryset.filter(room__block_id__in=allowed_block_ids)

        if room_id:
            queryset = queryset.filter(room_id=room_id)
        if block_id:
            parsed_block_id = _to_int(block_id)
            if parsed_block_id is None:
                return Response({'error': 'Please provide a valid block id.'}, status=400)
            if allowed_block_ids is not None and parsed_block_id not in allowed_block_ids:
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
            queryset = queryset.filter(room__block_id=block_id)
        if available_only:
            queryset = queryset.exclude(allocations__is_active=True)

        serializer = HostelBedSerializer(queryset.distinct(), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        if _is_hostel_warden(request.user):
            room = get_object_or_404(HostelRoom, pk=data.get('room'))
            if room.block_id not in _staff_block_ids(request.user):
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        serializer = HostelBedSerializer(data=data)
        if serializer.is_valid():
            serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)


class AdminHostelBedDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def put(self, request, pk):
        bed = get_object_or_404(HostelBed, pk=pk)
        if _is_hostel_warden(request.user) and bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        serializer = HostelBedSerializer(bed, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({'status': 200, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)

    def delete(self, request, pk):
        bed = get_object_or_404(HostelBed, pk=pk)
        if _is_hostel_warden(request.user) and bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        if bed.allocations.filter(is_active=True).exists():
            return Response({'error': 'This bed has an active allocation. Please vacate the allocation before deleting the bed.'}, status=400)
        try:
            bed.delete()
        except ProtectedError:
            return _protected_delete_error_response('bed')
        return Response({'status': 200, 'message': 'Bed deleted'})


class AdminHostelWardenAssignmentView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelWardenAssignment.objects.select_related('block', 'staff').all().order_by('block__name', 'staff__name')
        if _is_hostel_warden(request.user):
            queryset = queryset.filter(staff=request.user.staff_profile)
        block_id = request.query_params.get('block_id')
        if block_id:
            queryset = queryset.filter(block_id=block_id)
        serializer = HostelWardenAssignmentSerializer(queryset, many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot manage warden assignments.'}, status=403)
        serializer = HostelWardenAssignmentSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)

    def put(self, request):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot manage warden assignments.'}, status=403)
        assignment_id = request.data.get('assignment_id')
        assignment = get_object_or_404(HostelWardenAssignment, pk=assignment_id)
        serializer = HostelWardenAssignmentSerializer(assignment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({'status': 200, 'data': serializer.data})
        return _serializer_error_response(serializer.errors)

    def delete(self, request):
        if _is_hostel_warden(request.user):
            return Response({'error': 'Hostel wardens cannot manage warden assignments.'}, status=403)
        assignment_id = request.data.get('assignment_id')
        assignment = get_object_or_404(HostelWardenAssignment, pk=assignment_id)
        try:
            assignment.delete()
        except ProtectedError:
            return _protected_delete_error_response('warden assignment')
        return Response({'status': 200, 'message': 'Warden assignment deleted'})


class AdminHostelAllocationView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelAllocation.objects.select_related(
            'student',
            'bed',
            'bed__room',
            'bed__room__block',
            'academic_year',
        ).all()

        is_active = request.query_params.get('is_active')
        block_id = request.query_params.get('block_id')
        room_id = request.query_params.get('room_id')
        student_id = request.query_params.get('student_id')
        allowed_block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None

        if allowed_block_ids is not None:
            queryset = queryset.filter(bed__room__block_id__in=allowed_block_ids)

        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=(is_active == 'true'))
        if block_id:
            parsed_block_id = _to_int(block_id)
            if parsed_block_id is None:
                return Response({'error': 'Please provide a valid block id.'}, status=400)
            if allowed_block_ids is not None and parsed_block_id not in allowed_block_ids:
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
            queryset = queryset.filter(bed__room__block_id=block_id)
        if room_id:
            queryset = queryset.filter(bed__room_id=room_id)
        if student_id:
            queryset = queryset.filter(student__student_id__icontains=student_id)

        serializer = HostelAllocationSerializer(queryset.order_by('-is_active', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        if _is_hostel_warden(request.user):
            bed = get_object_or_404(HostelBed, pk=data.get('bed'))
            if bed.room.block_id not in _staff_block_ids(request.user):
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        serializer = HostelAllocationSerializer(data=data)
        if serializer.is_valid():
            bed = serializer.validated_data['bed']
            room = bed.room
            active_count = HostelAllocation.objects.filter(bed__room=room, is_active=True).count()
            if active_count >= room.capacity:
                return Response({'error': 'Room capacity reached. Increase capacity or choose another room.'}, status=400)

            allocation = serializer.save(created_by_name=_actor_name(request.user))
            out = HostelAllocationSerializer(allocation)
            return Response({'status': 201, 'data': out.data})
        return _serializer_error_response(serializer.errors)

    def put(self, request):
        allocation_id = request.data.get('allocation_id')
        allocation = get_object_or_404(HostelAllocation, pk=allocation_id)
        if _is_hostel_warden(request.user) and allocation.bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        serializer = HostelAllocationSerializer(allocation, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            out = HostelAllocationSerializer(updated)
            return Response({'status': 200, 'data': out.data})
        return _serializer_error_response(serializer.errors)

    def delete(self, request):
        allocation_id = request.data.get('allocation_id')
        allocation = get_object_or_404(HostelAllocation, pk=allocation_id)
        if _is_hostel_warden(request.user) and allocation.bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        try:
            allocation.delete()
        except ProtectedError:
            return _protected_delete_error_response('allocation')
        return Response({'status': 200, 'message': 'Allocation deleted'})


class AdminHostelAttendanceView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelAttendance.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'marked_by',
        ).all()

        block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None
        if block_ids is not None:
            queryset = queryset.filter(allocation__bed__room__block_id__in=block_ids)

        date_val = request.query_params.get('date')
        block_id = request.query_params.get('block_id')

        if date_val:
            queryset = queryset.filter(date=date_val)
        if block_id:
            queryset = queryset.filter(allocation__bed__room__block_id=block_id)

        serializer = HostelAttendanceSerializer(queryset.order_by('-date', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        user = request.user
        if _is_hostel_warden(user):
            allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
            if allocation.bed.room.block_id not in _staff_block_ids(user):
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        if hasattr(user, 'staff_profile'):
            data['marked_by'] = user.staff_profile.id

        serializer = HostelAttendanceSerializer(data=data)
        if serializer.is_valid():
            attendance, _ = HostelAttendance.objects.update_or_create(
                allocation=serializer.validated_data['allocation'],
                date=serializer.validated_data['date'],
                defaults={
                    'status': serializer.validated_data['status'],
                    'remarks': serializer.validated_data.get('remarks', ''),
                    'marked_by': serializer.validated_data.get('marked_by'),
                    'created_by_name': _actor_name(request.user),
                },
            )
            out = HostelAttendanceSerializer(attendance)
            return Response({'status': 200, 'data': out.data})
        return _serializer_error_response(serializer.errors)


class AdminHostelIncidentView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelIncident.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'reported_by',
        ).all()

        block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None
        if block_ids is not None:
            queryset = queryset.filter(allocation__bed__room__block_id__in=block_ids)

        resolved = request.query_params.get('resolved')
        block_id = request.query_params.get('block_id')

        if resolved in {'true', 'false'}:
            queryset = queryset.filter(resolved=(resolved == 'true'))
        if block_id:
            queryset = queryset.filter(allocation__bed__room__block_id=block_id)

        serializer = HostelIncidentSerializer(queryset.order_by('-occurred_at', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        user = request.user
        if _is_hostel_warden(user):
            allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
            if allocation.bed.room.block_id not in _staff_block_ids(user):
                return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        if hasattr(user, 'staff_profile'):
            data['reported_by'] = user.staff_profile.id

        serializer = HostelIncidentSerializer(data=data)
        if serializer.is_valid():
            incident = serializer.save(created_by_name=_actor_name(request.user))
            out = HostelIncidentSerializer(incident)
            return Response({'status': 201, 'data': out.data})
        return _serializer_error_response(serializer.errors)

    def put(self, request):
        incident_id = request.data.get('incident_id')
        incident = get_object_or_404(HostelIncident, pk=incident_id)
        if _is_hostel_warden(request.user) and incident.allocation.bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        serializer = HostelIncidentSerializer(incident, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            return Response({'status': 200, 'data': HostelIncidentSerializer(updated).data})
        return _serializer_error_response(serializer.errors)


class AdminHostelInOutView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrHostelStaff]

    def get(self, request):
        queryset = HostelInOutLog.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'recorded_by',
        ).all()

        block_ids = _staff_block_ids(request.user) if _is_hostel_warden(request.user) else None
        if block_ids is not None:
            queryset = queryset.filter(allocation__bed__room__block_id__in=block_ids)

        movement_type = (request.query_params.get('movement_type') or '').lower()
        block_id = request.query_params.get('block_id')
        allocation_id = request.query_params.get('allocation_id')
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        if movement_type in {'in', 'out'}:
            queryset = queryset.filter(movement_type=movement_type)
        if block_id:
            queryset = queryset.filter(allocation__bed__room__block_id=block_id)
        if allocation_id:
            queryset = queryset.filter(allocation_id=allocation_id)
        if date_from:
            queryset = queryset.filter(moved_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(moved_at__date__lte=date_to)

        serializer = HostelInOutLogSerializer(queryset.order_by('-moved_at', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        data = request.data.copy()
        allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
        if _is_hostel_warden(request.user) and allocation.bed.room.block_id not in _staff_block_ids(request.user):
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        if not allocation.is_active:
            return Response({'error': 'This allocation is inactive, so in/out entry cannot be recorded.'}, status=400)

        user = request.user
        if hasattr(user, 'staff_profile'):
            data['recorded_by'] = user.staff_profile.id

        serializer = HostelInOutLogSerializer(data=data)
        if serializer.is_valid():
            entry = serializer.save(created_by_name=_actor_name(request.user))
            out = HostelInOutLogSerializer(entry)
            return Response({'status': 201, 'data': out.data})
        return _serializer_error_response(serializer.errors)


class StaffHostelScopeMixin:
    @staticmethod
    def _staff_block_ids(user):
        return _staff_block_ids(user)


class StaffMyHostelDashboardView(APIView, StaffHostelScopeMixin):
    permission_classes = [IsAuthenticated, IsHostelStaffUserType]

    def get(self, request):
        block_ids = self._staff_block_ids(request.user)
        if not block_ids:
            return Response({'status': 200, 'data': {'blocks': [], 'active_students': 0, 'pending_incidents': 0}})

        blocks = HostelBlock.objects.filter(id__in=block_ids)
        active_students = HostelAllocation.objects.filter(is_active=True, bed__room__block_id__in=block_ids).count()
        pending_incidents = HostelIncident.objects.filter(resolved=False, allocation__bed__room__block_id__in=block_ids).count()

        return Response(
            {
                'status': 200,
                'data': {
                    'blocks': HostelBlockSerializer(blocks, many=True).data,
                    'active_students': active_students,
                    'pending_incidents': pending_incidents,
                },
            }
        )


class StaffHostelOccupancyView(APIView, StaffHostelScopeMixin):
    permission_classes = [IsAuthenticated, IsHostelStaffUserType]

    def get(self, request):
        block_ids = self._staff_block_ids(request.user)
        queryset = HostelAllocation.objects.select_related(
            'student',
            'bed',
            'bed__room',
            'bed__room__block',
            'academic_year',
        ).filter(bed__room__block_id__in=block_ids)

        is_active = request.query_params.get('is_active', 'true').lower()
        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=(is_active == 'true'))

        serializer = HostelAllocationSerializer(queryset.order_by('-is_active', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})


class StaffHostelAttendanceView(APIView, StaffHostelScopeMixin):
    permission_classes = [IsAuthenticated, IsHostelStaffUserType]

    def get(self, request):
        block_ids = self._staff_block_ids(request.user)
        queryset = HostelAttendance.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'marked_by',
        ).filter(allocation__bed__room__block_id__in=block_ids)

        date_val = request.query_params.get('date')
        if date_val:
            queryset = queryset.filter(date=date_val)

        serializer = HostelAttendanceSerializer(queryset.order_by('-date', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        block_ids = self._staff_block_ids(request.user)
        data = request.data.copy()
        allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
        if allocation.bed.room.block_id not in block_ids:
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        data['marked_by'] = request.user.staff_profile.id
        serializer = HostelAttendanceSerializer(data=data)
        if serializer.is_valid():
            attendance, _ = HostelAttendance.objects.update_or_create(
                allocation=serializer.validated_data['allocation'],
                date=serializer.validated_data['date'],
                defaults={
                    'status': serializer.validated_data['status'],
                    'remarks': serializer.validated_data.get('remarks', ''),
                    'marked_by': serializer.validated_data.get('marked_by'),
                    'created_by_name': _actor_name(request.user),
                },
            )
            return Response({'status': 200, 'data': HostelAttendanceSerializer(attendance).data})
        return _serializer_error_response(serializer.errors)


class StaffHostelIncidentView(APIView, StaffHostelScopeMixin):
    permission_classes = [IsAuthenticated, IsHostelStaffUserType]

    def get(self, request):
        block_ids = self._staff_block_ids(request.user)
        queryset = HostelIncident.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'reported_by',
        ).filter(allocation__bed__room__block_id__in=block_ids)

        resolved = request.query_params.get('resolved')
        if resolved in {'true', 'false'}:
            queryset = queryset.filter(resolved=(resolved == 'true'))

        return Response({'status': 200, 'data': HostelIncidentSerializer(queryset, many=True).data})

    def post(self, request):
        block_ids = self._staff_block_ids(request.user)
        data = request.data.copy()
        allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
        if allocation.bed.room.block_id not in block_ids:
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        data['reported_by'] = request.user.staff_profile.id
        serializer = HostelIncidentSerializer(data=data)
        if serializer.is_valid():
            incident = serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': HostelIncidentSerializer(incident).data})
        return _serializer_error_response(serializer.errors)

    def put(self, request):
        block_ids = self._staff_block_ids(request.user)
        incident_id = request.data.get('incident_id')
        incident = get_object_or_404(HostelIncident, pk=incident_id)

        if incident.allocation.bed.room.block_id not in block_ids:
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)

        serializer = HostelIncidentSerializer(incident, data=request.data, partial=True)
        if serializer.is_valid():
            updated = serializer.save()
            return Response({'status': 200, 'data': HostelIncidentSerializer(updated).data})
        return _serializer_error_response(serializer.errors)


class StaffHostelInOutView(APIView, StaffHostelScopeMixin):
    permission_classes = [IsAuthenticated, IsHostelStaffUserType]

    def get(self, request):
        block_ids = self._staff_block_ids(request.user)
        queryset = HostelInOutLog.objects.select_related(
            'allocation',
            'allocation__student',
            'allocation__bed',
            'allocation__bed__room',
            'allocation__bed__room__block',
            'recorded_by',
        ).filter(allocation__bed__room__block_id__in=block_ids)

        movement_type = (request.query_params.get('movement_type') or '').lower()
        allocation_id = request.query_params.get('allocation_id')
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        if movement_type in {'in', 'out'}:
            queryset = queryset.filter(movement_type=movement_type)
        if allocation_id:
            queryset = queryset.filter(allocation_id=allocation_id)
        if date_from:
            queryset = queryset.filter(moved_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(moved_at__date__lte=date_to)

        serializer = HostelInOutLogSerializer(queryset.order_by('-moved_at', '-id'), many=True)
        return Response({'status': 200, 'data': serializer.data})

    def post(self, request):
        block_ids = self._staff_block_ids(request.user)
        data = request.data.copy()
        allocation = get_object_or_404(HostelAllocation, pk=data.get('allocation'))
        if allocation.bed.room.block_id not in block_ids:
            return Response({'error': 'You can only access data for your assigned hostel block(s).'}, status=403)
        if not allocation.is_active:
            return Response({'error': 'This allocation is inactive, so in/out entry cannot be recorded.'}, status=400)

        data['recorded_by'] = request.user.staff_profile.id
        serializer = HostelInOutLogSerializer(data=data)
        if serializer.is_valid():
            entry = serializer.save(created_by_name=_actor_name(request.user))
            return Response({'status': 201, 'data': HostelInOutLogSerializer(entry).data})
        return _serializer_error_response(serializer.errors)


class StudentMyHostelView(APIView):
    permission_classes = [IsAuthenticated, IsStudentUserType]

    def get(self, request):
        if not hasattr(request.user, 'student_profile'):
            return Response({'error': 'Student profile not found'}, status=404)

        allocation = (
            HostelAllocation.objects.select_related(
                'student',
                'bed',
                'bed__room',
                'bed__room__block',
                'academic_year',
            )
            .filter(student=request.user.student_profile, is_active=True)
            .first()
        )

        if not allocation:
            return Response({'status': 200, 'data': {'has_hostel': False}})

        roommates_qs = HostelAllocation.objects.select_related('student').filter(
            bed__room=allocation.bed.room,
            is_active=True,
        ).exclude(student=allocation.student)

        attendance_qs = HostelAttendance.objects.filter(allocation=allocation).order_by('-date')[:30]
        incidents_qs = HostelIncident.objects.filter(allocation=allocation).order_by('-occurred_at')[:20]
        in_out_qs = HostelInOutLog.objects.filter(allocation=allocation).order_by('-moved_at')[:30]

        return Response(
            {
                'status': 200,
                'data': {
                    'has_hostel': True,
                    'allocation': HostelAllocationSerializer(allocation).data,
                    'roommates': [
                        {
                            'student_id': i.student.student_id,
                            'student_name': i.student.student_name,
                        }
                        for i in roommates_qs
                    ],
                    'attendance_recent': HostelAttendanceSerializer(attendance_qs, many=True).data,
                    'incidents_recent': HostelIncidentSerializer(incidents_qs, many=True).data,
                    'in_out_recent': HostelInOutLogSerializer(in_out_qs, many=True).data,
                    'today': timezone.now().date(),
                },
            }
        )
