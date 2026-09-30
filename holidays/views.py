from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import Holiday
from .serializers import HolidaySerializer, BulkHolidaySerializer
from staff.permissions import IsAdmin # Your custom permission

# ==========================================
# 1. ADMIN: MANAGE HOLIDAYS (CRUD + BULK)
# ==========================================
class HolidayManageView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """ View ALL Holidays (Admin View) """
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        
        holidays = Holiday.objects.all().order_by('date')

        if year:
            holidays = holidays.filter(date__year=year)
        if month:
            holidays = holidays.filter(date__month=month)

        serializer = HolidaySerializer(holidays, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """
        Create Single OR Multiple Holidays.
        
        Format 1 (Bulk):
        {
            "name": "Pongal Holidays",
            "applicable_for": "everyone",
            "dates": ["2026-01-14", "2026-01-15", "2026-01-16"]
        }

        Format 2 (Single - Standard):
        {
            "name": "Republic Day",
            "date": "2026-01-26",
            "applicable_for": "everyone"
        }
        """
        # A. Check for Bulk Format ("dates" list)
        if 'dates' in request.data and isinstance(request.data['dates'], list):
            bulk_serializer = BulkHolidaySerializer(data=request.data)
            if bulk_serializer.is_valid():
                name = bulk_serializer.validated_data['name']
                app_for = bulk_serializer.validated_data['applicable_for']
                dates = bulk_serializer.validated_data['dates']
                
                created_holidays = []
                errors = []

                for d in dates:
                    # Avoid duplicates for same date
                    if Holiday.objects.filter(date=d).exists():
                        errors.append(f"{d}: Already exists")
                        continue
                    
                    h = Holiday.objects.create(name=name, date=d, applicable_for=app_for)
                    created_holidays.append(h)
                
                return Response({
                    "status": 200, 
                    "message": f"Created {len(created_holidays)} holidays.",
                    "skipped_errors": errors
                })
            return Response(bulk_serializer.errors, status=400)

        # B. Standard Single Creation
        else:
            serializer = HolidaySerializer(data=request.data)
            if serializer.is_valid():
                serializer.save()
                return Response({"status": 200, "message": "Holiday Created", "data": serializer.data})
            return Response(serializer.errors, status=400)

    def put(self, request):
        """ 
        Edit Single OR Multiple Holidays.
        
        Format 1 (Bulk Update):
        [
            { "holiday_id": 5, "name": "New Name 1" },
            { "holiday_id": 6, "applicable_for": "teachers" }
        ]

        Format 2 (Single Update):
        { "holiday_id": 5, "name": "New Name", "date": "2026-01-27" }
        """
        data = request.data

        # A. Check for Bulk Format (List of dictionaries)
        if isinstance(data, list):
            updated_count = 0
            errors = []

            for item in data:
                holiday_id = item.get('holiday_id')
                if not holiday_id:
                    errors.append({"error": "holiday_id is required", "data": item})
                    continue

                try:
                    holiday = Holiday.objects.get(id=holiday_id)
                    serializer = HolidaySerializer(holiday, data=item, partial=True)
                    if serializer.is_valid():
                        serializer.save()
                        updated_count += 1
                    else:
                        errors.append({"holiday_id": holiday_id, "errors": serializer.errors})
                except Holiday.DoesNotExist:
                    errors.append({"holiday_id": holiday_id, "error": "Holiday not found"})

            return Response({
                "status": 200, 
                "message": f"Updated {updated_count} holidays.",
                "errors": errors if errors else None
            })

        # B. Standard Single Update
        else:
            holiday_id = data.get('holiday_id')
            holiday = get_object_or_404(Holiday, id=holiday_id)

            serializer = HolidaySerializer(holiday, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response({"status": 200, "message": "Holiday Updated Successfully"})
            
            return Response(serializer.errors, status=400)

    def delete(self, request):
        """ Delete a Holiday (By ID) """
        holiday_id = request.data.get('holiday_id')
        holiday = get_object_or_404(Holiday, id=holiday_id)
        holiday.delete()
        return Response({"status": 200, "message": "Holiday Deleted"})


class AdminHolidayPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        search = (request.query_params.get('search') or '').strip()
        year = (request.query_params.get('year') or '').strip()
        month = (request.query_params.get('month') or '').strip()
        applicable_for = (request.query_params.get('applicable_for') or '').strip()

        try:
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 10))
        except (TypeError, ValueError):
            return Response({"error": "Invalid page or page_size"}, status=400)

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        page_size = min(page_size, 100)

        holidays = Holiday.objects.all()

        if year:
            try:
                year_int = int(year)
                holidays = holidays.filter(date__year=year_int)
            except (TypeError, ValueError):
                return Response({"error": "Invalid year filter"}, status=400)

        if month:
            try:
                month_int = int(month)
                if month_int < 1 or month_int > 12:
                    return Response({"error": "Invalid month filter"}, status=400)
                holidays = holidays.filter(date__month=month_int)
            except (TypeError, ValueError):
                return Response({"error": "Invalid month filter"}, status=400)

        if applicable_for and applicable_for.lower() != 'all':
            valid_applicable_for = {choice[0] for choice in Holiday.APPLICABLE_CHOICES}
            if applicable_for not in valid_applicable_for:
                return Response({"error": "Invalid applicable_for filter"}, status=400)
            holidays = holidays.filter(applicable_for=applicable_for)

        if search:
            holidays = holidays.filter(name__icontains=search)

        holidays = holidays.order_by('date', 'id')

        total = holidays.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged = holidays[start:end]

        serializer = HolidaySerializer(paged, many=True)

        summary = {
            "total": total,
            "everyone": holidays.filter(applicable_for='everyone').count(),
            "students_only": holidays.filter(applicable_for='students_only').count(),
            "staff": holidays.filter(applicable_for='staff').count(),
            "teachers": holidays.filter(applicable_for='teachers').count(),
        }

        return Response({
            "status": 200,
            "data": serializer.data,
            "summary": summary,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1
            }
        })


# ==========================================
# 2. PUBLIC: VIEW HOLIDAYS (Smart Filter)
# ==========================================
class PublicHolidayView(APIView):
    """
    GET: View Holidays based on User Role.
    - Students see: Everyone + Students Only
    - Teachers see: Everyone + Teachers Only
    - Staff see:    Everyone + Staff Only
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        year = request.query_params.get('year')
        month = request.query_params.get('month')

        # 1. Determine Role & Filter
        if hasattr(user, 'student_profile'):
            filter_query = Q(applicable_for='everyone') | Q(applicable_for='students_only')
        
        elif hasattr(user, 'teacher_profile'):
            filter_query = Q(applicable_for='everyone') | Q(applicable_for='teachers')
        
        elif hasattr(user, 'staff_profile'):
            filter_query = Q(applicable_for='everyone') | Q(applicable_for='staff')
        
        else:
            # Fallback (e.g., Admin viewing this route)
            filter_query = Q(applicable_for='everyone')

        # 2. Fetch Data
        holidays = Holiday.objects.filter(filter_query).order_by('date')

        # 3. Apply Optional Date Filters
        if year:
            holidays = holidays.filter(date__year=year)
        if month:
            holidays = holidays.filter(date__month=month)

        serializer = HolidaySerializer(holidays, many=True)
        return Response({"status": 200, "role": "detected_automatically", "data": serializer.data})
