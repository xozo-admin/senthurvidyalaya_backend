from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db.models import Q
from datetime import date

from .models import LeaveRequest
from .serializers import LeaveRequestSerializer

from students.models import Enrollment
from academics.models import Section, ClassTeacher
from school.models import AcademicYear
from staff.permissions import IsAdmin

from django.contrib.auth import get_user_model
from notifications.utils import send_notification_to_users

User = get_user_model()

# =======================================================
# 1. APPLY / MANAGE OWN LEAVE (Student, Staff, Teacher)
# =======================================================
class ApplyLeaveView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def get_my_profile(self, user):
        if hasattr(user, 'student_profile'): return user.student_profile, 'student'
        elif hasattr(user, 'staff_profile'): return user.staff_profile, 'staff'
        elif hasattr(user, 'teacher_profile'): return user.teacher_profile, 'teacher'
        return None, None

    def get(self, request):
        """ 
        View My Leave History with Filters:
        - ?month=2 (February)
        - ?year=2026 (Year 2026)
        """
        profile, role = self.get_my_profile(request.user)
        if not profile: return Response({"error": "Profile not found"}, 403)

        # 1. Base Query (User Specific)
        if role == 'student':
            # Strict Restriction: Current Academic Year ONLY
            try:
                active_year = AcademicYear.objects.get(is_current=True)
                leaves = LeaveRequest.objects.filter(student=profile, academic_year=active_year)
            except AcademicYear.DoesNotExist:
                return Response({"error": "No Active Academic Year"}, 500)
                
        elif role == 'staff':
            # Full History
            leaves = LeaveRequest.objects.filter(staff=profile)
            
        elif role == 'teacher':
            # Full History
            leaves = LeaveRequest.objects.filter(teacher=profile)

        # 2. Apply Date Filters (Month & Year)
        req_month = request.query_params.get('month')
        req_year = request.query_params.get('year')

        if req_year:
            leaves = leaves.filter(start_date__year=req_year)
        
        if req_month:
            leaves = leaves.filter(start_date__month=req_month)

        # 3. Apply Status Filter (Optional)
        req_status = request.query_params.get('status')
        if req_status:
            leaves = leaves.filter(status__iexact=req_status)

        serializer = LeaveRequestSerializer(leaves.order_by('-created_at'), many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """ Apply for Leave """
        profile, role = self.get_my_profile(request.user)
        if not profile: return Response({"error": "Profile not found"}, 403)

        data = request.data
        academic_year = None

        if role == 'student':
            enrollment = Enrollment.objects.filter(student=profile, is_active=True).first()
            if enrollment:
                academic_year = enrollment.academic_year
            else:
                return Response({"error": "You are not enrolled in an active academic year."}, 400)

        leave_req = LeaveRequest(
            user_type=role,
            student=profile if role == 'student' else None,
            staff=profile if role == 'staff' else None,
            teacher=profile if role == 'teacher' else None,
            academic_year=academic_year,
            start_date=data.get('start_date'),
            end_date=data.get('end_date'),
            reason=data.get('reason'),
            proof_file=request.FILES.get('proof_file')
        )
        leave_req.save()
        # --- START NOTIFICATION BLOCK (New Request) ---
        try:
            recipients = []
            sender_id = "Unknown"

            # 1. Logic for Students: Notify Class Teacher
            if role == 'student' and enrollment:
                sender_id = profile.student_id
                # Find Class Teacher for this section & year
                ct_entry = ClassTeacher.objects.filter(
                    section=enrollment.section,
                    academic_year=academic_year
                ).first()
                if ct_entry and ct_entry.teacher.user:
                    recipients.append(ct_entry.teacher.user)

            # 2. Logic for Staff/Teachers: Notify Admin
            elif role in ['staff', 'teacher']:
                if role == 'teacher': sender_id = profile.teacher_id
                else: sender_id = profile.staff_id
                # Notify all Admins
                recipients = User.objects.filter(user_type__in=['admin', 'super_admin'])

            # 3. Send the Alert
            if recipients:
                send_notification_to_users(
                    users=recipients,
                    sender=request.user,
                    title=f"Leave Request: {role.capitalize()}",
                    message=f"New request from {profile.name if hasattr(profile, 'name') else profile.student_name} ({sender_id}). Reason: {leave_req.reason}",
                    notif_type="Leave Request"
                )
        except Exception as e:
            print(f"Notification Error: {e}")
        # --- END NOTIFICATION BLOCK ---

        return Response({"status": 200, "message": "Leave Request Submitted"})

    def put(self, request):
        """ Edit Leave (Only if Pending) """
        profile, role = self.get_my_profile(request.user)
        leave_id = request.data.get('leave_id')
        
        kwargs = {'id': leave_id}
        if role == 'student': kwargs['student'] = profile
        elif role == 'staff': kwargs['staff'] = profile
        elif role == 'teacher': kwargs['teacher'] = profile
        
        leave = get_object_or_404(LeaveRequest, **kwargs)

        if leave.status != 'Pending':
            return Response({"error": "Cannot edit. Request is already Processed."}, 400)

        if 'start_date' in request.data: leave.start_date = request.data['start_date']
        if 'end_date' in request.data: leave.end_date = request.data['end_date']
        if 'reason' in request.data: leave.reason = request.data['reason']
        if 'proof_file' in request.FILES: leave.proof_file = request.FILES['proof_file']
        
        leave.save()
        return Response({"status": 200, "message": "Leave Request Updated"})

    def delete(self, request):
        """ Delete Leave (Only if Pending) """
        profile, role = self.get_my_profile(request.user)
        leave_id = request.data.get('leave_id')

        kwargs = {'id': leave_id}
        if role == 'student': kwargs['student'] = profile
        elif role == 'staff': kwargs['staff'] = profile
        elif role == 'teacher': kwargs['teacher'] = profile
        
        leave = get_object_or_404(LeaveRequest, **kwargs)

        if leave.status != 'Pending':
            return Response({"error": "Cannot delete. Request is already Processed."}, 400)

        leave.delete()
        return Response({"status": 200, "message": "Leave Request Deleted"})


# =======================================================
# 2. ADMIN ACTION (Only for Staff & Teachers)
# =======================================================
class AdminLeaveActionView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """ 
        Admin View with Filters:
        - ?status=Pending (Default) - Shows tasks to do
        - ?month=2 (Shows leaves happening in Feb)
        - ?year=2026
        """
        # 1. Base Filters
        status = request.query_params.get('status', 'Pending')
        req_month = request.query_params.get('month')
        req_year = request.query_params.get('year')

        # 2. Initial Query (Exclude Students)
        leaves = LeaveRequest.objects.filter(
            user_type__in=['staff', 'teacher']
        )

        # 3. Apply Filters
        if status:
            leaves = leaves.filter(status__iexact=status)

        # LOGIC: Filter by START_DATE (When the leave actually happens)
        if req_year:
            leaves = leaves.filter(start_date__year=req_year)
        
        if req_month:
            leaves = leaves.filter(start_date__month=req_month)

        leaves = leaves.order_by('start_date')  # Sort by upcoming leaves

        serializer = LeaveRequestSerializer(leaves, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """ Approve/Reject Staff or Teacher Leave """
        leave_id = request.data.get('leave_id')
        action = request.data.get('action') 
        comment = request.data.get('comment', '')

        leave_req = get_object_or_404(LeaveRequest, id=leave_id)
        
        if leave_req.user_type == 'student':
            return Response({"error": "Admin cannot approve student leaves here."}, 403)

        leave_req.status = action
        leave_req.admin_comment = comment
        leave_req.approved_by_name = "Admin"
        leave_req.save()
        # --- START NOTIFICATION BLOCK (Admin Decision) ---
        try:
            target_user = None
            if leave_req.teacher: target_user = leave_req.teacher.user
            elif leave_req.staff: target_user = leave_req.staff.user
            
            if target_user:
                send_notification_to_users(
                    users=[target_user],
                    sender=request.user, # The Admin
                    title=f"Leave {action}",
                    message=f"Your leave request for {leave_req.start_date} has been {action}. Comment: {comment}",
                    notif_type="Leave Update"
                )
        except Exception as e:
            print(f"Notification Error: {e}")
        # --- END NOTIFICATION BLOCK ---
        
        return Response({"status": 200, "message": f"Request {action}"})


class AdminLeaveActionPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        status = (request.query_params.get('status', 'Pending') or '').strip()
        req_month = (request.query_params.get('month') or '').strip()
        req_year = (request.query_params.get('year') or '').strip()
        search = (request.query_params.get('search') or '').strip()
        user_type = (request.query_params.get('user_type') or '').strip()
        role = (request.query_params.get('role') or '').strip()

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

        leaves = LeaveRequest.objects.filter(user_type__in=['staff', 'teacher'])

        if status and status.lower() != 'all':
            leaves = leaves.filter(status__iexact=status)
        if req_year:
            leaves = leaves.filter(start_date__year=req_year)
        if req_month:
            leaves = leaves.filter(start_date__month=req_month)
        if user_type and user_type.lower() != 'all':
            leaves = leaves.filter(user_type__iexact=user_type)
        if role and role.lower() != 'all':
            leaves = leaves.filter(user_type__iexact=role)
        if search:
            leaves = leaves.filter(
                Q(reason__icontains=search)
                | Q(staff__name__icontains=search)
                | Q(staff__staff_id__icontains=search)
                | Q(teacher__name__icontains=search)
                | Q(teacher__teacher_id__icontains=search)
            )

        leaves = leaves.order_by('start_date', '-created_at')

        total = leaves.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        paged = leaves[start:end]

        serializer = LeaveRequestSerializer(paged, many=True)

        summary = {
            "pending": leaves.filter(status='Pending').count(),
            "approved": leaves.filter(status='Approved').count(),
            "rejected": leaves.filter(status='Rejected').count(),
            "total": total
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


# =======================================================
# 3. CLASS TEACHER ACTION (Only for Students)
# =======================================================
class TeacherApproveStudentLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """ 
        Teacher View with Filters:
        - ?status=Pending (Default)
        - ?month=2
        - ?year=2026
        """
        if not hasattr(request.user, 'teacher_profile'):
            return Response({"error": "Only teachers can access this"}, 403)
        
        teacher = request.user.teacher_profile
        status = request.query_params.get('status', 'Pending')
        req_month = request.query_params.get('month')
        req_year = request.query_params.get('year')

        # 1. Get My Class Students (Current Year)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            ct_record = ClassTeacher.objects.get(teacher=teacher, academic_year=active_year)
            my_section = ct_record.section
        except (AcademicYear.DoesNotExist, ClassTeacher.DoesNotExist):
            return Response({"status": 200, "data": [], "message": "Not a class teacher"})

        student_ids = Enrollment.objects.filter(
            section=my_section, 
            academic_year=active_year,
            is_active=True
        ).values_list('student', flat=True)

        # 2. Filter Leaves for these students
        leaves = LeaveRequest.objects.filter(
            student__in=student_ids,
            user_type='student'
        )

        # 3. Apply Filters
        if status:
            leaves = leaves.filter(status__iexact=status)

        # LOGIC: Filter by START_DATE (When the leave actually happens)
        if req_year:
            leaves = leaves.filter(start_date__year=req_year)
        if req_month:
            leaves = leaves.filter(start_date__month=req_month)

        leaves = leaves.order_by('start_date') # Sort by date so upcoming ones are first

        serializer = LeaveRequestSerializer(leaves, many=True)
        return Response({"status": 200, "data": serializer.data})

    def post(self, request):
        """ Approve/Reject Student Leave """
        if not hasattr(request.user, 'teacher_profile'):
            return Response({"error": "Unauthorized"}, 403)

        leave_id = request.data.get('leave_id')
        action = request.data.get('action')
        comment = request.data.get('comment', '')

        leave_req = get_object_or_404(LeaveRequest, id=leave_id, user_type='student')
        
        # Security: Double check student belongs to teacher's class
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            ct_record = ClassTeacher.objects.get(teacher=request.user.teacher_profile, academic_year=active_year)
            
            is_valid = Enrollment.objects.filter(
                student=leave_req.student,
                section=ct_record.section,
                academic_year=active_year,
                is_active=True
            ).exists()
            
            if not is_valid: return Response({"error": "Student not in your class"}, 403)
        except:
            return Response({"error": "Permission Denied"}, 403)

        leave_req.status = action
        leave_req.admin_comment = comment
        leave_req.approved_by_name = request.user.teacher_profile.name
        leave_req.save()
        
        # --- START NOTIFICATION BLOCK (Teacher Decision) ---
        try:
            if leave_req.student and leave_req.student.user:
                send_notification_to_users(
                    users=[leave_req.student.user],
                    sender=request.user, # The Class Teacher
                    title=f"Leave {action}",
                    message=f"Your leave request for {leave_req.start_date} has been {action} by {request.user.teacher_profile.name}.",
                    notif_type="Leave Update"
                )
        except Exception as e:
            print(f"Notification Error: {e}")
        # --- END NOTIFICATION BLOCK ---

        return Response({"status": 200, "message": f"Student Leave {action}"})
