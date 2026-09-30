from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
import datetime
from .utils import ensure_daily_tasks_exist

from .models import WorkOrder, WorkAssignment, RecurringTaskTemplate
from .serializers import WorkOrderSerializer, WorkAssignmentSerializer, RecurringTaskTemplateSerializer
from staff.models import NonTeachingStaff
from staff.permissions import IsAdmin

# =======================================================
# THE HELPER FUNCTION (Shared Logic)
# =======================================================
def ensure_daily_tasks_exist(date_obj=None):
    """
    Checks if Recurring Tasks for the given date exist.
    If not, creates them automatically.
    """
    if date_obj is None:
        date_obj = timezone.now().date()

    day_name = date_obj.strftime('%A') # e.g., 'Monday'

    # Find templates for this day
    templates = RecurringTaskTemplate.objects.filter(day_of_week__iexact=day_name)


    for template in templates:
        # Check if task already exists for this specific date to avoid duplicates
        already_exists = WorkAssignment.objects.filter(
            staff=template.staff,
            work_order__description=template.description,
            work_order__created_date=date_obj,
            work_order__is_recurring=True
        ).exists()

        if not already_exists:
            # Create the parent WorkOrder
            work_order = WorkOrder.objects.create(
    description=template.description,
    staff_type=template.staff.role,
    is_recurring=True,
    created_date=date_obj   # ⭐⭐⭐ IMPORTANT
)

            # Create the specific Assignment
            WorkAssignment.objects.create(
                work_order=work_order,
                staff=template.staff,
                status='Pending'
            )

# =======================================================
# 1. ADMIN: RECURRING SCHEDULE (Manage Templates)
# =======================================================
class AdminRecurringScheduleView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """ View the Timetable """
        day = request.query_params.get('day')
        schedules = RecurringTaskTemplate.objects.all()
        
        if day:
            schedules = schedules.filter(day_of_week__iexact=day)
            
        serializer = RecurringTaskTemplateSerializer(schedules, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """
        Expects this structure:
        {
            "staff_type": "external_staff",
            "monday": [
                { "staff_id": "3010", "description": "Clean Hall" },
                { "staff_id": "3011", "description": "Clean Garden" }
            ],
            "tuesday": [ ... ]
        }
        """
        data = request.data
        staff_type = data.get('staff_type') # Optional validation if you want to check role
        
        # List of valid day names to look for in the JSON
        valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        
        created_count = 0
        errors = []

        with transaction.atomic():
            # Loop through each possible day key
            for day_key in valid_days:
                day_tasks = data.get(day_key, []) # Get list for "monday", or empty list if missing
                
                # If the user provided tasks for this day
                if day_tasks:
                    # Convert 'monday' -> 'Monday' (Capitalized for DB consistency)
                    formatted_day = day_key.capitalize() 

                    for item in day_tasks:
                        s_id = item.get('staff_id')
                        desc = item.get('description')

                        if not s_id or not desc:
                            continue

                        try:
                            # 1. Get Staff
                            staff_obj = NonTeachingStaff.objects.get(staff_id=str(s_id))

                            # 2. Strict Role Check (Optional but recommended)
                            if staff_type and staff_obj.role != staff_type:
                                errors.append(f"Skipped {s_id}: Role mismatch ({staff_obj.role} != {staff_type})")
                                continue

                            # 3. Create Template
                            RecurringTaskTemplate.objects.create(
                                staff=staff_obj,
                                day_of_week=formatted_day,
                                description=desc
                            )
                            created_count += 1

                        except NonTeachingStaff.DoesNotExist:
                            errors.append(f"Skipped {s_id}: ID not found")
                        except Exception as e:
                            # Handle duplicate constraint (same staff, same day, same task)
                            errors.append(f"Skipped {s_id} on {formatted_day}: Task already exists")

        return Response({
            "status": 200,
            "message": f"Successfully scheduled {created_count} tasks.",
            "errors": errors
        })

    def delete(self, request):
        """ Delete a Schedule Entry """
        s_id = request.data.get('schedule_id')
        entry = get_object_or_404(RecurringTaskTemplate, id=s_id)
        entry.delete()
        return Response({"status": 200, "message": "Schedule Deleted"})

# =======================================================
# 2. ADMIN: BULK ASSIGN (Ad-hoc / Manual Tasks)
# =======================================================
class AdminBulkAssignTaskView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        data = request.data
        target_role = data.get('staff_type') 
        task_list = data.get('tasks', [])

        if not target_role or not task_list:
            return Response({"error": "staff_type and tasks are required"}, 400)

        created_count = 0
        errors = []

        with transaction.atomic():
            for item in task_list:
                description = item.get('description')
                raw_ids = item.get('staff_id') 

                if not description or not raw_ids:
                    continue

                target_ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]

                # Create the Work Order
                work_order = WorkOrder.objects.create(
                    description=description,
                    staff_type=target_role,
                    is_recurring=False # Manual task
                )

                for s_id in target_ids:
                    try:
                        staff_obj = NonTeachingStaff.objects.get(staff_id=str(s_id))
                        
                        # --- STRICT CHECK ---
                        if staff_obj.role != target_role:
                            errors.append({
                                "id": s_id,
                                "error": f"Role Mismatch: Staff is '{staff_obj.role}', but task is for '{target_role}'"
                            })
                            continue 
                        # --------------------

                        WorkAssignment.objects.create(
                            work_order=work_order,
                            staff=staff_obj,
                            status='Pending'
                        )
                    except NonTeachingStaff.DoesNotExist:
                        errors.append({"id": s_id, "error": "ID not found"})
                
                created_count += 1

        status_code = 200 if created_count > 0 else 400
        return Response({
            "status": status_code,
            "message": f"Processed tasks. Created {created_count} orders.",
            "errors": errors
        }, status=status_code)

# =======================================================
# 3. ADMIN: MANAGE TASKS (View, Edit, Delete)
# =======================================================
class AdminTaskManagementView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """ 
        Params: ?staff_type=external_staff&date=2026-01-08 
        """
        s_type = request.query_params.get('staff_type')
        date_str = request.query_params.get('date')

        # --- TRIGGER HELPER: Ensure tasks exist for the view date ---
        target_date = timezone.now().date()
        if date_str:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        ensure_daily_tasks_exist(target_date)
        # ------------------------------------------------------------

        orders = WorkOrder.objects.all().order_by('-created_date')

        if s_type:
            orders = orders.filter(staff_type=s_type)
        if date_str:
            orders = orders.filter(created_date=date_str)

        response_data = []
        for order in orders:
            assignments = order.assignments.all()
            staff_details = []
            
            for assign in assignments:
                staff_details.append({
                    "assignment_id": assign.id,
                    "staff_id": assign.staff.staff_id,
                    "staff_name": assign.staff.name,
                    "status": assign.status,
                    "proof_url": assign.proof_file.url if assign.proof_file else None,
                    "completion_note": assign.completion_note
                })

            response_data.append({
                "task_id": order.id,
                "description": order.description,
                "date": order.created_date,
                "is_recurring": order.is_recurring,
                "assignments": staff_details
            })

        return Response({"status": 200, "data": response_data})

    def put(self, request):
        task_id = request.data.get('task_id')
        new_desc = request.data.get('new_description')
        
        work_order = get_object_or_404(WorkOrder, id=task_id)
        work_order.description = new_desc
        work_order.save()
        return Response({"status": 200, "message": "Description updated"})

    def delete(self, request):
        task_id = request.data.get('task_id')
        assignment_id = request.data.get('assignment_id')

        if task_id:
            get_object_or_404(WorkOrder, id=task_id).delete()
            return Response({"status": 200, "message": "Task deleted for everyone"})
        elif assignment_id:
            get_object_or_404(WorkAssignment, id=assignment_id).delete()
            return Response({"status": 200, "message": "Staff removed from task"})
            
        return Response({"error": "Missing ID"}, 400)


class AdminTaskManagementPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """
        Params:
        - staff_type
        - date (YYYY-MM-DD)
        - search
        - status (Pending/Completed/In Progress/Overdue)
        - page (default: 1)
        - page_size (default: 10, max: 100)
        """
        s_type = (request.query_params.get('staff_type') or '').strip()
        date_str = (request.query_params.get('date') or '').strip()
        search = (request.query_params.get('search') or '').strip()
        status_filter = (request.query_params.get('status') or '').strip()

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

        target_date = timezone.now().date()
        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        ensure_daily_tasks_exist(target_date)

        orders = WorkOrder.objects.all().order_by('-created_date', '-id')

        if s_type and s_type.lower() != 'all':
            orders = orders.filter(staff_type=s_type)
        if date_str:
            orders = orders.filter(created_date=date_str)
        if search:
            orders = orders.filter(
                Q(description__icontains=search)
                | Q(assignments__staff__name__icontains=search)
                | Q(assignments__staff__staff_id__icontains=search)
            )
        if status_filter and status_filter.lower() != 'all':
            orders = orders.filter(assignments__status__iexact=status_filter)

        orders = orders.prefetch_related('assignments__staff').distinct()

        total = orders.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged_orders = orders[start:end]

        response_data = []
        for order in paged_orders:
            assignments = order.assignments.all()
            staff_details = []

            for assign in assignments:
                staff_details.append({
                    "assignment_id": assign.id,
                    "staff_id": assign.staff.staff_id,
                    "staff_name": assign.staff.name,
                    "status": assign.status,
                    "proof_url": assign.proof_file.url if assign.proof_file else None,
                    "completion_note": assign.completion_note
                })

            response_data.append({
                "task_id": order.id,
                "description": order.description,
                "date": order.created_date,
                "is_recurring": order.is_recurring,
                "assignments": staff_details
            })

        filtered_assignments = WorkAssignment.objects.filter(work_order__in=orders)
        summary = {
            "total": total,
            "completed": filtered_assignments.filter(status='Completed').count(),
            "pending": filtered_assignments.filter(status='Pending').count(),
            "inProgress": filtered_assignments.filter(status='In Progress').count(),
            "recurring": orders.filter(is_recurring=True).count()
        }

        return Response({
            "status": 200,
            "data": response_data,
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

# =======================================================
# 4. STAFF: MY TASKS (Get, Post, Put, Delete)
# =======================================================
# staff_work/views.py (Partial Update)

class StaffTaskOperationView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def get_staff_assignment(self, request, assignment_id):
        if not hasattr(request.user, 'staff_profile'):
            return None
        return get_object_or_404(WorkAssignment, id=assignment_id, staff=request.user.staff_profile)

    def get(self, request):
        """ 
        View My Tasks.
        - Default: Shows TODAY's tasks only.
        - Optional: ?date=2026-01-08 (Shows specific date history)
        """
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)

        # ✅ Determine target date
        today = timezone.now().date()
        date_param = request.query_params.get('date')

        target_date = today
        if date_param:
            target_date = datetime.datetime.strptime(date_param, "%Y-%m-%d").date()

        # ✅ Ensure tasks exist for that date
        ensure_daily_tasks_exist(target_date)

        # Base Query
        my_tasks = WorkAssignment.objects.filter(staff=user.staff_profile)

        if date_param:
            # CASE A: User asked for history
            my_tasks = my_tasks.filter(work_order__created_date=target_date)
        else:
            # CASE B: Default View (Today)
            my_tasks = my_tasks.filter(work_order__created_date=today)

        # Order by latest first
        my_tasks = my_tasks.order_by('-work_order__created_date')
        
        data = []
        for item in my_tasks:
            data.append({
                "assignment_id": item.id,
                "description": item.work_order.description,
                "date": item.work_order.created_date,
                "is_recurring": item.work_order.is_recurring,
                "status": item.status,
                "note": item.completion_note,
                "proof_url": item.proof_file.url if item.proof_file else None
            })
        return Response({"status": 200, "tasks": data})

    # =====================================================
    # ✅ MODIFICATION ALLOWED ONLY FOR TODAY
    # =====================================================

    def post(self, request):
        a_id = request.data.get('assignment_id')
        assignment = self.get_staff_assignment(request, a_id)
        
        if not assignment:
            return Response({"error": "Task not found or denied"}, 403)

        # 🔒 Guard
        today = timezone.now().date()
        if assignment.work_order.created_date != today:
            return Response({"error": "You can update tasks only for today."}, 403)

        assignment.proof_file = request.FILES.get('proof_file')
        assignment.completion_note = request.data.get('notes', '')
        assignment.status = 'Completed'
        assignment.completed_at = timezone.now()
        assignment.save()

        return Response({"status": 200, "message": "Submitted"})

    def put(self, request):
        a_id = request.data.get('assignment_id')
        assignment = self.get_staff_assignment(request, a_id)

        if not assignment:
            return Response({"error": "Task not found"}, 403)

        # 🔒 Guard
        today = timezone.now().date()
        if assignment.work_order.created_date != today:
            return Response({"error": "You can update tasks only for today."}, 403)

        if 'proof_file' in request.FILES:
            assignment.proof_file = request.FILES['proof_file']
        if 'notes' in request.data:
            assignment.completion_note = request.data['notes']
            
        assignment.save()
        return Response({"status": 200, "message": "Submission Updated"})

    def delete(self, request):
        a_id = request.data.get('assignment_id')
        assignment = self.get_staff_assignment(request, a_id)

        if not assignment:
            return Response({"error": "Task not found"}, 403)

        # 🔒 Guard
        today = timezone.now().date()
        if assignment.work_order.created_date != today:
            return Response({"error": "You can update tasks only for today."}, 403)

        assignment.proof_file = None
        assignment.completion_note = ''
        assignment.status = 'Pending'
        assignment.completed_at = None
        assignment.save()

        return Response({"status": 200, "message": "Submission Deleted. Task is Pending."})



## staff to view his own dashboard
class StaffSimpleDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied. Staff Only."}, 403)
        
        staff_profile = user.staff_profile
        today = timezone.now().date()

        # 1. Ensure Recurring Tasks are Generated for Today
        ensure_daily_tasks_exist(today)

        # ====================================================
        # 2. TODAY'S SUMMARY
        # ====================================================
        todays_assignments = WorkAssignment.objects.filter(
            staff=staff_profile, 
            work_order__created_date=today
        )

        total_today = todays_assignments.count()
        completed_today = todays_assignments.filter(status='Completed').count()
        pending_today = total_today - completed_today

        # ====================================================
        # 3. MONTHLY PERFORMANCE
        # ====================================================
        monthly_assignments = WorkAssignment.objects.filter(
            staff=staff_profile,
            work_order__created_date__month=today.month,
            work_order__created_date__year=today.year
        )

        total_month = monthly_assignments.count()
        completed_month = monthly_assignments.filter(status='Completed').count()

        # Calculate Rate
        completion_rate_str = "0%"
        if total_month > 0:
            rate = round((completed_month / total_month) * 100)
            completion_rate_str = f"{rate}%"

        # ====================================================
        # 4. FINAL RESPONSE
        # ====================================================
        data = {
            "today_summary": {
                "total_tasks": total_today,
                "completed": completed_today,
                "pending": pending_today
            },
            "performance_month": {
                "month": today.strftime('%B'), # e.g., "February"
                "completion_rate": completion_rate_str,
                "total_assigned": total_month,
                "total_completed": completed_month
            }
        }

        return Response(data, status=200)
