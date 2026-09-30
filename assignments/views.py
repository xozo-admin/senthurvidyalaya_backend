import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db.models import Q, Prefetch

# Models
from .models import Assignment, AssignmentSubmission
from .serializers import (
    AssignmentSerializer, 
    SubmissionSerializer, 
    StudentFeedSerializer,
    StudentAssignmentCombinedSerializer
)
from teachers.models import Teacher, TeacherAllocation
from subjects.models import Subject
from academics.models import Standard, Section, ClassTeacher
from school.models import AcademicYear
from timetable.models import TimetableSlot  # <--- KEY CHANGE: Import Timetable
from notifications.utils import send_notification_to_users
from django.contrib.auth import get_user_model  # <--- NEW IMPORT
User = get_user_model()                         # <--- Define 'User' variable
from students.models import Enrollment          # <--- NEW IMPORT (To find students)
from collections import defaultdict

# ==========================================
# HELPER: STRICT PERMISSION CHECK (TIMETABLE-BASED)
# ==========================================
def check_teacher_permission(user, cls_name, sec_name, sub_name, academic_year):
    """
    Returns True if the user is authorized to post assignments for this SPECIFIC Section.
    Authorization:
    1. Class Teacher of this Section.
    2. Subject Teacher (Has a Timetable Slot for this Section).
    """
    try:
        if not hasattr(user, 'teacher_profile'):
            return False
            
        teacher = user.teacher_profile
        
        # 1. Find Section Object (To check ID)
        section = Section.objects.get(standard__name=cls_name, name=sec_name)
        
        # 2. Check Class Teacher (Highest Priority)
        is_class_teacher = ClassTeacher.objects.filter(
            teacher=teacher,
            section=section,
            academic_year=academic_year
        ).exists()
        
        if is_class_teacher: return True

        # 3. Check Timetable (Strict Subject Teacher Logic)
        # "Does this teacher have a slot for THIS Subject in THIS Section?"
        has_slot = TimetableSlot.objects.filter(
            academic_year=academic_year,
            teacher=teacher,
            section=section,
            subject__name__iexact=sub_name # Check specific subject
        ).exists()
        
        return has_slot
        
    except Exception:
        return False

# ==========================================
# 1. TEACHER: MANAGE ASSIGNMENTS (CRUD)
# ==========================================
class TeacherAssignmentManagerView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # A. POST: Create New Assignment (Specific Class & Section)
    def post(self, request):
        user = request.user
        
        # 0. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 1. READ INPUTS
        cls = request.data.get('class_name') 
        sec = request.data.get('section')    
        sub = request.data.get('subject')    
        due_date = request.data.get('due_date') 

        if not all([cls, sec, sub, due_date]):
            return Response({"error": "Missing required fields: class_name, section, subject, due_date"}, 400)

        # 2. STRICT PERMISSION CHECK (With Year)
        if not check_teacher_permission(user, cls, sec, sub, active_year):
             return Response({"error": f"You are not assigned to teach {sub} for Class {cls}-{sec} in {active_year.name}."}, 403)

        serializer = AssignmentSerializer(data=request.data)
        if serializer.is_valid():
            # 1. SAVE THE ASSIGNMENT (Crucial: assign it to the variable 'assignment')
            assignment = serializer.save(
                teacher=user,
                academic_year=active_year
            )

            # 2. SEND NOTIFICATIONS
            try:
                # Find the 3 students we know exist
                target_students = User.objects.filter(
                    student_profile__enrollments__standard__name=cls,
                    student_profile__enrollments__section__name=sec,
                    student_profile__enrollments__academic_year=active_year,
                    student_profile__enrollments__is_active=True
                )

                send_notification_to_users(
                    users=target_students,
                    sender=user,
                    title=f"New Assignment: {sub}",
                    message=f"Teacher {user.teacher_profile.name} posted: '{assignment.title}'. Due: {assignment.due_date}",
                    notif_type="Assignment",
                    data={
                        "screen": "student_assignments",
                        "permission": "assignments",
                        "class": cls,
                        "section": sec,
                        "subject": sub,
                    },
                )
            except Exception as e:
                print(f"Notification Error: {e}")

            return Response({"message": "Assignment posted successfully", "data": serializer.data}, 201)

            return Response({"message": "Assignment posted successfully", "data": serializer.data}, 201)
        return Response(serializer.errors, 400)

    # B. GET: View Assignments (Filterable by Class & Section)
    def get(self, request):
        if not hasattr(request.user, 'teacher_profile'):
            return Response({"error": "Unauthorized"}, 403)
            
        # 0. Get Active Year (Strict Mode for Teachers)
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)

        # 1. Extract Query Parameters
        req_class = request.query_params.get('class_name')
        req_section = request.query_params.get('section')
        req_subject = request.query_params.get('subject')
        req_date = request.query_params.get('date')

        # --- NEW MANDATORY CHECK ---
        if not req_subject:
            return Response({"message": "subject is mandatory"}, 400)
            
        if not req_class or not req_section:
            return Response({"message": "class_name and section are mandatory"}, 400)

        # 2. STRICT PERMISSION CHECK (Now guaranteed to run)
        if not check_teacher_permission(request.user, req_class, req_section, req_subject, active_year):
            return Response({
                "error": f"Permission Denied: You are not assigned to teach '{req_subject}' for Class {req_class}-{req_section} in {active_year.name}."
            }, 403)

        # 3. Proceed with Fetching Data (Filtered by Active Year & Required Params)
        my_assignments = Assignment.objects.filter(
            teacher=request.user,
            academic_year=active_year,
            class_name=req_class,
            section=req_section,
            subject__iexact=req_subject
        )

        # ========================================================
        # SCENARIO A: Fetch Specific Date (Daily Assignments)
        # ========================================================
        if req_date:
            my_assignments = my_assignments.filter(created_at__date=req_date).order_by('-created_at')
            serializer = AssignmentSerializer(my_assignments, many=True)
            return Response({
                "status": 200,
                "view_mode": "daily_assignments",
                "year": active_year.name,
                "filters_applied": {
                    "class": req_class, "section": req_section, "subject": req_subject, "date": req_date
                },
                "data": serializer.data
            }, 200)

        # ========================================================
        # SCENARIO B: No Date -> Return Calendar Dates
        # ========================================================
        else:
            unique_dates = my_assignments.values_list('created_at__date', flat=True).distinct()
            calendar_data = defaultdict(list)
            for d in unique_dates:
                if d:
                    month_key = f"month {d.month}"
                    calendar_data[month_key].append(d.strftime("%d-%m-%Y"))
                    
            return Response({
                "status": 200,
                "view_mode": "calendar_overview",
                "year": active_year.name,
                "filters_applied": {
                    "class": req_class, "section": req_section, "subject": req_subject
                },
                "data": dict(calendar_data)
            }, 200)

    # C. PUT: Edit Description Only (STRICT CHECK)
    def put(self, request):
        assign_id = request.data.get('assignment_id')
        new_desc = request.data.get('description')
        
        if not assign_id or not new_desc:
            return Response({"error": "assignment_id and description are required"}, 400)

        # 0. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Academic Year."}, 500)

        # 1. Find the Assignment (Must be created by this teacher AND in Active Year)
        assignment = get_object_or_404(
            Assignment, 
            id=assign_id, 
            teacher=request.user,
            academic_year=active_year # <--- Strict Year Check
        )

        # 2. STRICT CHECK: Does the teacher STILL teach this?
        if not check_teacher_permission(request.user, assignment.class_name, assignment.section, assignment.subject, active_year):
             return Response({
                 "error": f"Permission Denied: You are no longer assigned to teach this subject in {active_year.name}."
             }, 403)

        # 3. Save Changes
        assignment.description = new_desc
        assignment.save()

        # --- START NOTIFICATION BLOCK ---
        try:
            # 1. Find students again (since it's an update, we fetch them again)
            target_students = User.objects.filter(
                student_profile__enrollments__standard__name=assignment.class_name,
                student_profile__enrollments__section__name=assignment.section,
                student_profile__enrollments__academic_year=active_year,
                student_profile__enrollments__is_active=True
            )

            # 2. Send Update Alert
            send_notification_to_users(
                users=target_students,
                sender=request.user,
                title=f"Update: {assignment.subject}",
                message=f"The assignment '{assignment.title}' has been updated. Please review the new description.",
                notif_type="Assignment Update",
                data={
                    "screen": "student_assignments",
                    "permission": "assignments",
                    "class": assignment.class_name,
                    "section": assignment.section,
                    "subject": assignment.subject,
                },
            )
        except Exception as e:
            print(f"Notification Error: {e}")
        # --- END NOTIFICATION BLOCK ---

        return Response({"message": "Description updated successfully."})

    # D. DELETE: Delete Whole Assignment (STRICT CHECK)
    def delete(self, request):
        assign_id = request.query_params.get('assignment_id')
        
        if not assign_id:
             return Response({"error": "assignment_id is required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Academic Year."}, 500)

        # 1. Find Assignment (Active Year only)
        assignment = get_object_or_404(
            Assignment, 
            id=assign_id, 
            teacher=request.user,
            academic_year=active_year
        )

        # 2. STRICT CHECK
        if not check_teacher_permission(request.user, assignment.class_name, assignment.section, assignment.subject, active_year):
             return Response({
                 "error": "Permission Denied: Allocation mismatch."
             }, 403)

        # 3. Delete File
        if assignment.attachment and os.path.isfile(assignment.attachment.path):
            os.remove(assignment.attachment.path)
            
        # 4. Delete DB Record
        assignment.delete()
        return Response({"message": "Assignment and file deleted successfully."}, 200)


# ==========================================
# 2. TEACHER: FILE OPERATIONS ONLY
# ==========================================
class TeacherFileOperationsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def delete(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "Year error"}, 500)

        assign_id = request.query_params.get('assignment_id')
        # Restrict to Active Year to prevent accidental deletion of history
        assignment = get_object_or_404(
            Assignment, 
            id=assign_id, 
            teacher=request.user,
            academic_year=active_year
        )

        if assignment.attachment:
            if os.path.isfile(assignment.attachment.path):
                os.remove(assignment.attachment.path)
            
            assignment.attachment = None
            assignment.save()
            return Response({"message": "File removed. Description remains."}, 200)
        else:
            return Response({"message": "No file to delete."}, 200)

    def post(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "Year error"}, 500)

        assign_id = request.data.get('assignment_id')
        new_file = request.FILES.get('attachment')

        if not new_file:
            return Response({"error": "No file provided"}, 400)

        assignment = get_object_or_404(
            Assignment, 
            id=assign_id, 
            teacher=request.user,
            academic_year=active_year
        )

        if assignment.attachment:
            return Response({"error": "File already exists. Delete it first."}, 400)

        assignment.attachment = new_file
        assignment.save()
        return Response({"message": "File uploaded successfully."}, 200)


# ==========================================
# 3. EXISTING STUDENT & GRADING VIEWS (REFACTORED)
# ==========================================
class StudentAssignmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Check Profile
        try:
            student = request.user.student_profile
        except AttributeError:
             return Response({"error": "Unauthorized. Student profile missing."}, 403)
             
        # 2. Check Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year configured"}, 500)
        
        # --- KEY CHANGE: Use Enrollment (Promotion-Proof) ---
        from students.models import Enrollment
        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year, is_active=True)
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
             return Response({"error": "You are not enrolled in any active class for this year."}, 400)
        # ----------------------------------------------------

        # 3. Filter using REAL Relationships (The "Right" Way)
        assignments = Assignment.objects.filter(
            class_name=my_section.standard.name, # Dynamic lookup
            section=my_section.name,             # Dynamic lookup
            academic_year=active_year
        ).order_by('-created_at')

        serializer = AssignmentSerializer(assignments, many=True)
        return Response(serializer.data, 200)


class StudentAssignmentCombinedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Check Profile
        try:
            student = request.user.student_profile
        except AttributeError:
            return Response({"error": "Unauthorized. Student profile missing."}, 403)

        # 2. Check Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Year configured"}, 500)

        # --- KEY CHANGE: Use Enrollment (Promotion-Proof) ---
        from students.models import Enrollment
        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year, is_active=True)
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
            return Response({"error": "You are not enrolled in any active class for this year."}, 400)
        # ----------------------------------------------------

        assignments = Assignment.objects.filter(
            class_name=my_section.standard.name,
            section=my_section.name,
            academic_year=active_year
        ).order_by('-created_at')

        submissions_qs = AssignmentSubmission.objects.filter(student=request.user)
        assignments = assignments.prefetch_related(
            Prefetch('submissions', queryset=submissions_qs, to_attr='student_submissions')
        )

        serializer = StudentAssignmentCombinedSerializer(assignments, many=True, context={'request': request})
        return Response(serializer.data, 200)

class SubmitAssignmentView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser] 

    def post(self, request):
        # 1. Check Profile
        try:
            student = request.user.student_profile
        except AttributeError:
             return Response({"error": "Unauthorized. Student profile missing."}, 403)

        # 2. Check Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year configured"}, 500)

        # --- KEY CHANGE: Use Enrollment (Promotion-Proof) ---
        from students.models import Enrollment
        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year, is_active=True)
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
             return Response({"error": "You are not enrolled in any active class."}, 400)
        # ----------------------------------------------------

        assign_id = request.data.get('assignment_id')
        
        # 3. Validate Assignment exists in Active Year
        assignment = get_object_or_404(Assignment, id=assign_id, academic_year=active_year)

        # 4. Validate Class Match (Dynamic)
        # We compare the assignment's target class vs the student's ACTUAL current class
        if (assignment.class_name != my_section.standard.name or 
            assignment.section != my_section.name):
            return Response({"error": "This assignment is not for your class"}, 403)

        AssignmentSubmission.objects.create(
            assignment=assignment,
            student=request.user,
            file=request.FILES.get('file')
        )
        return Response({"message": "Submitted successfully"}, 201)
        

class GradeSubmissionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not hasattr(request.user, 'teacher_profile'): 
             return Response({"error": "Unauthorized"}, 403)

        sub_id = request.data.get('submission_id')
        marks = request.data.get('marks')

        submission = get_object_or_404(AssignmentSubmission, id=sub_id)
        
        submission.marks = marks
        submission.graded_by = request.user
        submission.save()

        return Response({"message": "Marks updated successfully"}, 200)

class MonthlyReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, 'teacher_profile'): 
             return Response({"error": "Unauthorized"}, 403)

        # Reports often need historical context, so we don't strictly filter by Active Year here
        # unless 'year' param is used.
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        cls = request.query_params.get('class')
        sec = request.query_params.get('section')
        sub = request.query_params.get('subject')

        submissions = AssignmentSubmission.objects.filter(
            assignment__class_name=cls,
            assignment__section=sec,
            assignment__subject=sub,
            submitted_at__month=month,
            submitted_at__year=year
        ).select_related('student')

        report_data = []
        for s in submissions:
            report_data.append({
                "student_id": s.student.id,
                "name": s.student.username,
                "marks": s.marks if s.marks is not None else "Pending"
            })

        return Response({"data": report_data}, 200)


# ==========================================
# 4. STUDENT: ASSIGNMENT DASHBOARD
# ==========================================

class StudentAssignmentFeedView(APIView):
    """
    GET: View Assignments for ACTIVE YEAR only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            student = request.user.student_profile
            active_year = AcademicYear.objects.get(is_current=True) # <--- Year Safety
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
             return Response({"error": "No Active Year"}, 500)

        # --- KEY CHANGE: Use Enrollment (Promotion-Proof) ---
        from students.models import Enrollment
        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year, is_active=True)
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
             return Response({"error": "You are not enrolled in any active class."}, 400)
        # ----------------------------------------------------

        # 1. Base Filter: Assignments for MY Section & ACTIVE YEAR
        assignments = Assignment.objects.filter(
            class_name=my_section.standard.name,
            section=my_section.name,
            academic_year=active_year # <--- Filter
        )

        # 2. Filters (Date & Subject)
        req_date = request.query_params.get('date')
        req_subject = request.query_params.get('subject')
        
        # --- NEW MANDATORY CHECK ---
        if not req_subject:
            return Response({"message": "subject is mandatory"}, 400)

        # Apply the subject filter (since it's now guaranteed to exist)
        assignments = assignments.filter(subject__iexact=req_subject)

        # ========================================================
        # SCENARIO A: Fetch Specific Date (Daily Assignments)
        # ========================================================
        if req_date:
            assignments = assignments.filter(created_at__date=req_date).order_by('-created_at')
            serializer = StudentFeedSerializer(
                assignments, 
                many=True,
                context={'request': request} 
            )
            return Response({
                "status": 200, 
                "view_mode": "daily_assignments",
                "student_class": f"{my_section.standard.name}-{my_section.name}",
                "year": active_year.name,
                "data": serializer.data
            }, 200)

        # ========================================================
        # SCENARIO B: No Date -> Return Calendar Dates
        # ========================================================
        else:
            unique_dates = assignments.values_list('created_at__date', flat=True).distinct()
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
            }, 200)

class StudentSubmissionManagerView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # A. GET: View My Submission
    def get(self, request):
        assign_id = request.query_params.get('assignment_id')
        if not assign_id:
            return Response({"error": "Please provide ?assignment_id=..."}, 400)

        try:
            # We restrict viewing to Active Year assignments usually, but for checking history 
            # of a specific ID, checking ID + Student is usually enough security.
            submission = AssignmentSubmission.objects.get(
                student=request.user, 
                assignment_id=assign_id
            )
            serializer = SubmissionSerializer(submission)
            return Response({"data": serializer.data}, 200)
        except AssignmentSubmission.DoesNotExist:
            return Response({"message": "You have not submitted this assignment yet."}, 404)

    # B. POST: Create Submission
    def post(self, request):
        assign_id = request.data.get('assignment_id')
        desc = request.data.get('description')
        file_obj = request.FILES.get('file')

        if not assign_id or not file_obj:
            return Response({"error": "assignment_id and file are required"}, 400)

        try:
            active_year = AcademicYear.objects.get(is_current=True)
            # Ensure we are submitting to an Active Year assignment
            assignment = get_object_or_404(Assignment, id=assign_id, academic_year=active_year)
        except:
            return Response({"error": "Assignment or Year error"}, 400)

        # --- KEY CHANGE: Use Enrollment (Promotion-Proof) ---
        student = request.user.student_profile
        from students.models import Enrollment
        try:
            enrollment = Enrollment.objects.get(student=student, academic_year=active_year, is_active=True)
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
             return Response({"error": "You are not enrolled in any active class."}, 400)
        # ----------------------------------------------------

        # Validate Class match
        if (assignment.class_name != my_section.standard.name or 
            assignment.section != my_section.name):
            return Response({"error": "This assignment is not for your class."}, 403)

        if AssignmentSubmission.objects.filter(student=request.user, assignment=assignment).exists():
            return Response({"error": "Already submitted. Use PUT to edit."}, 400)

        submission = AssignmentSubmission.objects.create(
            assignment=assignment,
            student=request.user,
            description=desc,
            file=file_obj
        )
        return Response({"message": "Assignment submitted successfully", "id": submission.id}, 201)

    # C. PUT: Edit Submission
    def put(self, request):
        sub_id = request.data.get('submission_id')
        new_desc = request.data.get('description')
        new_file = request.FILES.get('file')

        if not sub_id: return Response({"error": "submission_id required"}, 400)

        # Allow editing only if the assignment is still in Active Year? 
        # Yes, good practice to prevent changing history.
        submission = get_object_or_404(
            AssignmentSubmission, 
            id=sub_id, 
            student=request.user,
            assignment__academic_year__is_current=True # <--- Strict Check
        )

        if new_desc: submission.description = new_desc
        if new_file: submission.file = new_file
        
        submission.save()
        return Response({"message": "Submission updated successfully."})

    # D. DELETE: Two Modes
    def delete(self, request):
        sub_id = request.query_params.get('submission_id')
        delete_type = request.query_params.get('type') 
        
        if not sub_id: return Response({"error": "submission_id required"}, 400)

        # Restrict deletion to Active Year
        submission = get_object_or_404(
            AssignmentSubmission, 
            id=sub_id, 
            student=request.user,
            assignment__academic_year__is_current=True # <--- Strict Check
        )
        
        if delete_type == 'file':
            if submission.file and os.path.isfile(submission.file.path):
                os.remove(submission.file.path)
            
            submission.file = None 
            submission.save()
            return Response({"message": "File deleted."}, 200)
        else:
            if submission.file and os.path.isfile(submission.file.path):
                os.remove(submission.file.path)
            submission.delete()
            return Response({"message": "Submission deleted completely."}, 200)

# ==========================================
# 2.5 TEACHER: VIEW CLASS TRACKER FOR ASSIGNMENT
# ==========================================
class TeacherSubmissionListView(APIView):
    """
    GET: List ALL students for a specific Assignment ID with their status.
    - Status: "Completed" (with description & file) or "Pending".
    - Shows student description, hides marks.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        assign_id = request.query_params.get('assignment_id')
        if not assign_id:
            return Response({"error": "assignment_id is required"}, 400)

        # 1. Get Active Year & Assignment
        try:
            active_year = AcademicYear.objects.get(is_current=True)
            assignment = Assignment.objects.get(id=assign_id, academic_year=active_year)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year found."}, 500)
        except Assignment.DoesNotExist:
            return Response({"error": "Assignment not found."}, 404)

        # 2. STRICT PERMISSION CHECK
        # Reuse the helper to ensure this teacher actually teaches this class
        if not check_teacher_permission(request.user, assignment.class_name, assignment.section, assignment.subject, active_year):
             return Response({"error": "Permission Denied: You do not have access to this class."}, 403)

        # 3. Fetch ALL Students in this Class (Using Enrollment)
        from students.models import Enrollment
        
        enrollments = Enrollment.objects.filter(
            standard__name=assignment.class_name,
            section__name=assignment.section,
            academic_year=active_year,
            is_active=True
        ).select_related('student')

        # 4. Fetch Existing Submissions
        submissions = AssignmentSubmission.objects.filter(assignment=assignment)
        submission_map = {sub.student.student_profile.student_id: sub for sub in submissions}

        # 5. Build the Report List
        report_data = []
        for enroll in enrollments:
            student = enroll.student
            sid = student.student_id
            
            sub_obj = submission_map.get(sid)
            
            if sub_obj:
                # Student HAS submitted
                report_data.append({
                    "student_id": sid,
                    "student_name": student.student_name,
                    "status": "Completed",
                    "submission_id": sub_obj.id,
                    "submitted_at": sub_obj.submitted_at,
                    "file_url": sub_obj.file.url if sub_obj.file else None,
                    "description": sub_obj.description  # <--- Added
                })
            else:
                # Student has NOT submitted
                report_data.append({
                    "student_id": sid,
                    "student_name": student.student_name,
                    "status": "Pending",
                    "submission_id": None,
                    "submitted_at": None,
                    "file_url": None,
                    "description": None # <--- Added (Empty)
                })
        
        return Response({
            "status": 200,
            "assignment_title": assignment.title,
            "class": f"{assignment.class_name}-{assignment.section}",
            "total_students": len(report_data),
            "submitted_count": len(submissions),
            "pending_count": len(report_data) - len(submissions),
            "data": report_data
        }, 200)
