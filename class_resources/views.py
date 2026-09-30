import os
from django.db.models import Count, Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404

from .models import ClassResource
from .serializers import ClassResourceSerializer
from subjects.models import Subject
from school.models import AcademicYear  
from academics.models import ClassTeacher 

# --- NEW IMPORT FOR STUDENT VIEW ---
from students.models import Enrollment
from django.contrib.auth import get_user_model
from notifications.utils import send_notification_to_users

User = get_user_model()

# ==========================================
# HELPER: Get Class Teacher's Section (YEAR-SAFE)
# ==========================================
def get_my_section(user, academic_year):
    """
    Returns the Section if the user is the assigned Class Teacher
    for the specific Academic Year.
    """
    if hasattr(user, 'teacher_profile'):
        try:
            # Query the ClassTeacher table for this year
            ct_record = ClassTeacher.objects.get(
                teacher=user.teacher_profile, 
                academic_year=academic_year
            )
            return ct_record.section
        except ClassTeacher.DoesNotExist:
            return None
    return None

# ==========================================
# 1. MAIN RESOURCE MANAGER (POST, GET, PUT, DELETE WHOLE)
# ==========================================
class ClassResourceView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # A. POST: Create Resource
    def post(self, request):
        # 1. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, 500)

        # 2. Check Permission (Year-Safe)
        my_section = get_my_section(request.user, active_year)
        if not my_section:
            return Response({"error": "Access Denied. You are not a Class Teacher for the current session."}, 403)

        title = request.data.get('title')
        desc = request.data.get('description', '')
        subject_name = request.data.get('subject') # Optional
        file_obj = request.FILES.get('file')

        if not title:
            return Response({"error": "Title is required"}, 400)

        # Handle Subject (If provided, find it; else None)
        subject_obj = None
        if subject_name:
            try:
                subject_obj = Subject.objects.get(name__iexact=subject_name, standard=my_section.standard)
            except Subject.DoesNotExist:
                return Response({"error": f"Subject '{subject_name}' not found for your class."}, 404)

        # 3. Create with Active Year
        resource = ClassResource.objects.create(
            academic_year=active_year, # <--- Injected Here
            section=my_section,
            posted_by=request.user,
            subject=subject_obj,
            title=title,
            description=desc,
            file=file_obj
        )

        try:
            target_students = User.objects.filter(
                student_profile__enrollments__section=my_section,
                student_profile__enrollments__academic_year=active_year,
                student_profile__enrollments__is_active=True,
            ).distinct()

            subject_label = subject_obj.name if subject_obj else "Class"
            send_notification_to_users(
                users=target_students,
                sender=request.user,
                title=f"New Resource: {subject_label}",
                message=f"{request.user.teacher_profile.name} posted '{resource.title}'.",
                notif_type="Class Resource",
                data={
                    "screen": "student_assignments",
                    "permission": "assignments",
                    "class": my_section.standard.name,
                    "section": my_section.name,
                    "subject": subject_label,
                },
            )
        except Exception as e:
            print(f"Notification Error: {e}")

        return Response({"message": "Resource posted successfully", "id": resource.id}, 201)

    # B. GET: View Resources (Filtered by Subject & Date & Year)
    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        my_section = get_my_section(request.user, active_year)
        if not my_section:
            return Response({"error": "Access Denied. You are not a Class Teacher."}, 403)

        # Base Filter: My Section & Active Year Only
        resources = ClassResource.objects.filter(
            section=my_section, 
            academic_year=active_year # <--- Year Filter
        )

        # Filter 1: Date
        req_date = request.query_params.get('date')
        if req_date:
            resources = resources.filter(created_at__date=req_date)

        # Filter 2: Subject
        req_subject = request.query_params.get('subject')
        if req_subject:
            resources = resources.filter(subject__name__iexact=req_subject)
        else:
            resources = resources.filter(subject__isnull=True)

        serializer = ClassResourceSerializer(resources.order_by('-created_at'), many=True)
        return Response({"data": serializer.data, "year": active_year.name}, 200)

    # C. PUT: Edit Description Only
    def put(self, request):
        res_id = request.data.get('resource_id')
        new_desc = request.data.get('description')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        my_section = get_my_section(request.user, active_year)
        
        # Ensure we only edit resources from the Active Year
        resource = get_object_or_404(
            ClassResource, 
            id=res_id, 
            section=my_section, 
            academic_year=active_year # <--- Strict Check
        )

        if new_desc is not None:
            resource.description = new_desc
            resource.save()
            return Response({"message": "Description updated."})
        return Response({"error": "Description required"}, 400)

    # D. DELETE: Delete WHOLE Resource (Record + File)
    def delete(self, request):
        res_id = request.query_params.get('resource_id')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        my_section = get_my_section(request.user, active_year)
        
        # Ensure we only delete resources from the Active Year
        resource = get_object_or_404(
            ClassResource, 
            id=res_id, 
            section=my_section,
            academic_year=active_year # <--- Strict Check
        )

        # Delete file from storage
        if resource.file and os.path.isfile(resource.file.path):
            os.remove(resource.file.path)
            
        resource.delete()
        return Response({"message": "Resource deleted successfully."}, 200)


# ==========================================
# 2. FILE MANAGER (DELETE FILE ONLY, REPOST FILE)
# ==========================================
class ClassResourceFileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # A. DELETE: File Only (Keep Description)
    def delete(self, request):
        res_id = request.query_params.get('resource_id')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "Year Error"}, 500)

        my_section = get_my_section(request.user, active_year)
        
        resource = get_object_or_404(
            ClassResource, 
            id=res_id, 
            section=my_section,
            academic_year=active_year
        )

        if resource.file:
            if os.path.isfile(resource.file.path):
                os.remove(resource.file.path)
            
            resource.file = None # Clear link in DB
            resource.save()
            return Response({"message": "File deleted. Description preserved."}, 200)
        return Response({"error": "No file to delete"}, 400)

    # B. POST: Repost/Upload File to existing resource
    def post(self, request):
        res_id = request.data.get('resource_id')
        new_file = request.FILES.get('file')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
            return Response({"error": "Year Error"}, 500)

        my_section = get_my_section(request.user, active_year)
        
        resource = get_object_or_404(
            ClassResource, 
            id=res_id, 
            section=my_section,
            academic_year=active_year
        )

        if not new_file: return Response({"error": "No file provided"}, 400)
        
        if resource.file:
            return Response({"error": "File already exists. Delete it first."}, 400)
            
        resource.file = new_file
        resource.save()
        return Response({"message": "File uploaded successfully."})


# ==========================================
# 3. SUMMARY VIEW (CARD DETAILS)
# ==========================================
class ClassResourceSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        my_section = get_my_section(request.user, active_year)
        if not my_section:
            return Response({"error": "Access Denied. You are not a Class Teacher."}, 403)

        resources = ClassResource.objects.filter(
            section=my_section,
            academic_year=active_year,
        )

        summary = resources.aggregate(
            total_resources=Count('id'),
            general_resources=Count('id', filter=Q(subject__isnull=True)),
            subject_resources=Count('id', filter=Q(subject__isnull=False)),
            unique_subjects=Count('subject', distinct=True, filter=Q(subject__isnull=False)),
        )

        return Response({
            "data": {
                "class_name": my_section.standard.name,
                "section": my_section.name,
                "academic_year": active_year.name,
                "total_resources": summary["total_resources"] or 0,
                "general_resources": summary["general_resources"] or 0,
                "subject_resources": summary["subject_resources"] or 0,
                "unique_subjects": summary["unique_subjects"] or 0,
            }
        }, 200)


# ==========================================
# 4. ALL RESOURCES VIEW (GENERAL + SUBJECTS FOR CLASS TEACHER)
# ==========================================
class ClassResourceAllView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

        my_section = get_my_section(request.user, active_year)
        if not my_section:
            return Response({"error": "Access Denied. You are not a Class Teacher."}, 403)

        resources = ClassResource.objects.filter(
            section=my_section,
            academic_year=active_year,
        )

        req_date = request.query_params.get('date')
        if req_date:
            resources = resources.filter(created_at__date=req_date)

        serializer = ClassResourceSerializer(resources.order_by('-created_at'), many=True)
        return Response({"data": serializer.data, "year": active_year.name}, 200)


# ==========================================
# 5. STUDENT VIEW (UPDATED FOR ENROLLMENT)
# ==========================================

class StudentClassResourceView(APIView):
    """
    GET: View Class Resources for the Student's Section (ACTIVE YEAR ONLY).
    Uses Enrollment for Year-Safety.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Identify Student & Active Year
        try:
            student = request.user.student_profile
            active_year = AcademicYear.objects.get(is_current=True)
        except AttributeError:
            return Response({"error": "Access Denied. Students only."}, 403)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year"}, 500)

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

        # 3. Base Filter: Resources for MY Section & ACTIVE YEAR
        resources = ClassResource.objects.filter(
            section=my_section,           # Dynamic: From Enrollment
            academic_year=active_year     # <--- Strict Filter
        )

        # 4. Filter by Subject
        req_subject = request.query_params.get('subject')
        if req_subject:
            resources = resources.filter(subject__name__iexact=req_subject)
        else:
            resources = resources.filter(subject__isnull=True)

        # 5. Filter by Date (Optional)
        req_date = request.query_params.get('date')
        if req_date:
            resources = resources.filter(created_at__date=req_date)

        serializer = ClassResourceSerializer(resources.order_by('-created_at'), many=True)
        return Response({
            "data": serializer.data, 
            "year": active_year.name
        }, 200)
