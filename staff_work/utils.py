import datetime
from django.utils import timezone
from .models import RecurringTaskTemplate, WorkOrder, WorkAssignment

def ensure_daily_tasks_exist(date_obj=None):
    """
    Checks if Recurring Tasks for the given date have been created.
    If not, creates them.
    """
    if date_obj is None:
        date_obj = timezone.now().date()

    # 1. What day is it? (e.g., 'Monday')
    day_name = date_obj.strftime('%A')

    # 2. Find all templates for this day
    templates = RecurringTaskTemplate.objects.filter(day_of_week=day_name)

    for template in templates:
        # 3. Check if we already created this task for THIS specific date
        # We check: Is there a WorkOrder with this description, for this staff, on this date?
        already_exists = WorkAssignment.objects.filter(
            staff=template.staff,
            work_order__description=template.description,
            work_order__created_date=date_obj,
            work_order__is_recurring=True
        ).exists()

        if not already_exists:
            # 4. Create the Real Task
            work_order = WorkOrder.objects.create(
                description=template.description,
                staff_type=template.staff.role, # Auto-fill role
                is_recurring=True
                # created_date defaults to today
            )
            
            WorkAssignment.objects.create(
                work_order=work_order,
                staff=template.staff,
                status='Pending'
            )
            print(f"Auto-generated task for {template.staff.name} on {date_obj}")