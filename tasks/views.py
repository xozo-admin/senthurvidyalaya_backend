from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from teachers.permissions import IsTeacher
from django.shortcuts import get_object_or_404

# Models
from .models import ToDoTask
from .serializers import (
    ToDoCreateSerializer, 
    ToDoListSerializer, 
    ToDoDetailSerializer, 
    StudentTaskSerializer
)
from academics.models import Standard, Section, ClassTeacher
from subjects.models import Subject
from school.models import AcademicYear
from timetable.models import TimetableSlot   # <--- REPLACES TeacherAllocation
from students.models import Enrollment       # <--- NEW IMPORT for Student View
from collections import defaultdict
from django.contrib.auth import get_user_model
from notifications.utils import send_notification_to_users

User = get_user_model()

# ==========================================
# HELPER: STRICT PERMISSION CHECK (YEAR-SAFE)
# Matches Assignments Module Logic
# ==========================================
def check_task_permission(user, cls_name, sec_name, sub_name, academic_year):
    """
    Returns True if the user is authorized to post tasks for this specific class/subject.
    Checks:
    1. Is Class Teacher? (Highest Priority)
    2. Has Timetable Slot? (Subject Teacher)
    """
    try:
        if not hasattr(user, 'teacher_profile'):
            return False
            
        teacher = user.teacher_profile
        
        # 1. Resolve Section Object
        # We need the actual object to check foreign keys
        try:
            section = Section.objects.get(standard__name=cls_name, name=sec_name)
        except Section.DoesNotExist:
            return False

        # 2. Check Class Teacher (Highest Priority)
        # If they are the class teacher, they can post tasks for ANY subject usually, 
        # or you can restrict it. Assuming Class Teacher has broad access:
        if ClassTeacher.objects.filter(
            teacher=teacher, 
            section=section, 
            academic_year=academic_year
        ).exists():
            return True

        # 3. Check Timetable (Strict Subject Teacher Logic)
        # "Does this teacher have a slot for THIS Subject in THIS Section?"
        return TimetableSlot.objects.filter(
            academic_year=academic_year,
            teacher=teacher,
            section=section,
            subject__name__iexact=sub_name # Check specific subject
        ).exists()

    except Exception:
        return False

# ==========================================
# 1. POST API: Create a To-Do
# ==========================================
class CreateToDoView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        # 1. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 2. Extract Data for Validation
        cls = request.data.get('class_name')
        sec = request.data.get('section_name')
        sub = request.data.get('subject_name')

        if not all([cls, sec, sub]):
            return Response({"error": "class_name, section_name, subject_name are required"}, 400)

        # 3. STRICT PERMISSION CHECK (Timetable/ClassTeacher)
        if not check_task_permission(request.user, cls, sec, sub, active_year):
             return Response({"error": f"Permission Denied: You are not assigned to teach {sub} for Class {cls}-{sec} in {active_year.name}."}, 403)

        # 4. Save Task
        serializer = ToDoCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            # Inject Active Year
            task = serializer.save(academic_year=active_year)

            try:
                target_students = User.objects.filter(
                    student_profile__enrollments__section=task.section,
                    student_profile__enrollments__academic_year=active_year,
                    student_profile__enrollments__is_active=True,
                ).distinct()

                send_notification_to_users(
                    users=target_students,
                    sender=request.user,
                    title=f"New To-Do: {task.subject.name}",
                    message=(
                        f"{request.user.teacher_profile.name} posted "
                        f"'{task.title}' for {task.date}."
                    ),
                    notif_type="To-Do Task",
                    data={
                        "screen": "student_todo",
                        "permission": "todo",
                        "class": cls,
                        "section": sec,
                        "subject": sub,
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")

            return Response({
                "status": 200,
                "message": "Task posted successfully",
                "date": task.date,
                "task_number": task.task_number
            })
        return Response(serializer.errors, status=400)

# ==========================================
# 2. GET API: View Tasks Summary (For a specific Class & Date)
# ==========================================
class TeacherToDoListView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get(self, request):
        # 1. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        subject_name = request.query_params.get('subject')
        date_str = request.query_params.get('date') # Made optional

        if not all([class_name, section_name, subject_name]):
            return Response({"error": "Missing params: class, section, subject"}, 400)

        # 2. Permission Check (Strict)
        if not check_task_permission(request.user, class_name, section_name, subject_name, active_year):
             return Response({"error": "Permission Denied: Allocation mismatch."}, 403)

        # 3. Base Query (Filter without date first)
        base_tasks = ToDoTask.objects.filter(
            teacher=request.user.teacher_profile,
            section__standard__name=class_name,
            section__name=section_name,
            subject__name__iexact=subject_name,
            academic_year=active_year
        )

        # SCENARIO A: Fetch Specific Date
        if date_str:
            tasks = base_tasks.filter(date=date_str).order_by('task_number')
            serializer = ToDoListSerializer(tasks, many=True)
            return Response({
                "status": 200,
                "view_mode": "daily_tasks",
                "count": tasks.count(),
                "year": active_year.name,
                "tasks": serializer.data
            })
            
        # SCENARIO B: Return Calendar Dates
        else:
            unique_dates = base_tasks.values_list('date', flat=True).distinct()
            calendar_data = defaultdict(list)
            for d in unique_dates:
                if d:
                    month_key = f"month {d.month}"
                    calendar_data[month_key].append(d.strftime("%d-%m-%Y"))
                    
            return Response({
                "status": 200,
                "view_mode": "calendar_overview",
                "year": active_year.name,
                "data": dict(calendar_data)
            })

# ==========================================
# 3. GET/PUT/DELETE API: Manage Specific Task
# ==========================================
class TeacherToDoDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_object(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return None

        date_str = request.query_params.get('date')
        task_num = request.query_params.get('task_number')

        if not date_str or not task_num:
            return None

        try:
            # Restrict to Active Year to prevent editing history
            return ToDoTask.objects.get(
                teacher=request.user.teacher_profile,
                date=date_str,
                task_number=task_num,
                academic_year=active_year # <--- Year Filter
            )
        except ToDoTask.DoesNotExist:
            return None

    def get(self, request):
        task = self.get_object(request)
        if not task:
            return Response({"error": "Task not found (or belongs to a different academic year)"}, 404)
        
        serializer = ToDoDetailSerializer(task)
        return Response({"status": 200, "data": serializer.data})

    def put(self, request):
        task = self.get_object(request)
        if not task:
            return Response({"error": "Task not found (or belongs to a different academic year)"}, 404)

        # Allow partial updates
        task.title = request.data.get('title', task.title)
        task.teacher_note = request.data.get('teacher_note', task.teacher_note)
        task.priority = request.data.get('priority', task.priority)
        task.estimated_time = request.data.get('estimated_time', task.estimated_time)
        task.save()

        return Response({"status": 200, "message": "Task updated successfully"})

    def delete(self, request):
        task = self.get_object(request)
        if not task:
            return Response({"error": "Task not found (or belongs to a different academic year)"}, 404)

        task.delete()
        return Response({"status": 200, "message": "Task deleted successfully"})

# ==========================================
# 4. STUDENT VIEW (REFACTORED for Enrollment)
# ==========================================
class StudentTaskView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Identify the Student & Year
        try:
            student = request.user.student_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 2. Get Enrolled Section (Source of Truth)
        # Replaces: if not student.section ...
        try:
            enrollment = Enrollment.objects.get(
                student=student, 
                academic_year=active_year, 
                is_active=True
            )
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
            return Response({"error": "You are not enrolled in any active class."}, 400)

        # 3. Get Filters (Date made optional)
        req_date = request.query_params.get('date')
        req_subject = request.query_params.get('subject')

        if not req_subject:
            return Response({"error": "Missing param: subject"}, 400)

        # 4. Base Query (Filter without date first)
        base_tasks = ToDoTask.objects.filter(
            section=my_section,              
            subject__name__iexact=req_subject, 
            academic_year=active_year        
        )

        # SCENARIO A: Fetch Specific Date
        if req_date:
            tasks = base_tasks.filter(date=req_date).order_by('priority', 'task_number')
            serializer = StudentTaskSerializer(tasks, many=True)
            return Response({
                "status": 200, 
                "view_mode": "daily_tasks",
                "student_class": f"{my_section.standard.name}-{my_section.name}",
                "year": active_year.name,
                "data": serializer.data
            })
            
        # SCENARIO B: Return Calendar Dates
        else:
            unique_dates = base_tasks.values_list('date', flat=True).distinct()
            calendar_data = defaultdict(list)
            for d in unique_dates:
                if d:
                    month_key = f"month {d.month}"
                    calendar_data[month_key].append(d.strftime("%d-%m-%Y"))
                    
            return Response({
                "status": 200,
                "view_mode": "calendar_overview",
                "student_class": f"{my_section.standard.name}-{my_section.name}",
                "year": active_year.name,
                "data": dict(calendar_data)
            })

# ==========================================
# 5. STUDENT VIEW (ALL SUBJECTS)
# ==========================================
class StudentTaskAllView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Identify the Student & Year
        try:
            student = request.user.student_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 2. Get Enrolled Section (Source of Truth)
        try:
            enrollment = Enrollment.objects.get(
                student=student,
                academic_year=active_year,
                is_active=True
            )
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
            return Response({"error": "You are not enrolled in any active class."}, 400)

        # 3. Optional Filters
        req_date = request.query_params.get('date')
        req_subject = request.query_params.get('subject')

        # 4. Base Query (Filter without date first)
        base_tasks = ToDoTask.objects.filter(
            section=my_section,
            academic_year=active_year
        )

        if req_subject:
            base_tasks = base_tasks.filter(subject__name__iexact=req_subject)

        # SCENARIO A: Fetch Specific Date
        if req_date:
            tasks = base_tasks.filter(date=req_date).order_by('priority', 'task_number')
            serializer = StudentTaskSerializer(tasks, many=True)
            return Response({
                "status": 200,
                "view_mode": "daily_tasks",
                "student_class": f"{my_section.standard.name}-{my_section.name}",
                "year": active_year.name,
                "data": serializer.data
            })

        # SCENARIO B: Return Calendar Dates
        unique_dates = base_tasks.values_list('date', flat=True).distinct()
        calendar_data = defaultdict(list)
        for d in unique_dates:
            if d:
                month_key = f"month {d.month}"
                calendar_data[month_key].append(d.strftime("%d-%m-%Y"))

        return Response({
            "status": 200,
            "view_mode": "calendar_overview",
            "student_class": f"{my_section.standard.name}-{my_section.name}",
            "year": active_year.name,
            "data": dict(calendar_data)
        })

# ==========================================
# 6. STUDENT TASK SUMMARY (COUNTS)
# ==========================================
class StudentTaskSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Identify the Student & Year
        try:
            student = request.user.student_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 2. Get Enrolled Section (Source of Truth)
        try:
            enrollment = Enrollment.objects.get(
                student=student,
                academic_year=active_year,
                is_active=True
            )
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
            return Response({"error": "You are not enrolled in any active class."}, 400)

        req_date = request.query_params.get('date')

        tasks_qs = ToDoTask.objects.filter(
            section=my_section,
            academic_year=active_year
        )

        if req_date:
            tasks_qs = tasks_qs.filter(date=req_date)

        total = tasks_qs.count()

        subject_counts = defaultdict(int)
        for subject_name in tasks_qs.values_list('subject__name', flat=True):
            if subject_name:
                subject_counts[subject_name] += 1

        subject_items = [
            {"subject": name, "count": count}
            for name, count in subject_counts.items()
        ]

        return Response({
            "status": 200,
            "year": active_year.name,
            "student_class": f"{my_section.standard.name}-{my_section.name}",
            "total": total,
            "subjects": subject_items
        })
