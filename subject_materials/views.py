import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db import models 

from .models import SubjectMaterial
from .serializers import SubjectMaterialSerializer
from class_resources.models import ClassResource
from class_resources.serializers import ClassResourceSerializer

# --- NEW IMPORTS FOR ARCHITECTURE ---
from academics.models import Section, ClassTeacher
from timetable.models import TimetableSlot
from students.models import Enrollment
from subjects.models import Subject
from school.models import AcademicYear

# ==========================================
# HELPER: Check Subject Teacher Permission (YEAR-SAFE)
# Matches Tasks & Assignments Logic
# ==========================================
def check_permission(user, cls_name, sec_name, sub_name, academic_year):
    """
    Returns True if 'user' is authorized to post materials for this class/subject.
    Checks: 
    1. Class Teacher (Access to all subjects in their section)
    2. Subject Teacher (Timetable Slot)
    """
    try:
        if not hasattr(user, 'teacher_profile'): return False
        
        teacher = user.teacher_profile
        
        # 1. Resolve Section Object (Needed for Foreign Keys)
        try:
            section = Section.objects.get(standard__name=cls_name, name=sec_name)
        except Section.DoesNotExist:
            return False

        # 2. Check Class Teacher (Highest Priority)
        if ClassTeacher.objects.filter(
            teacher=teacher, 
            section=section, 
            academic_year=academic_year
        ).exists():
            return True

        # 3. Check Timetable (Subject Teacher)
        return TimetableSlot.objects.filter(
            academic_year=academic_year,
            teacher=teacher,
            section=section,
            subject__name__iexact=sub_name 
        ).exists()

    except Exception:
        return False

# ==========================================
# 1. MANAGE MATERIALS (CRUD)
# ==========================================
class SubjectMaterialView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # A. POST: Create Material (Strict Check)
    def post(self, request):
        # 0. Get Active Year
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except AcademicYear.DoesNotExist:
            return Response({"error": "No Active Academic Year Configured"}, 500)

        user = request.user
        cls = request.data.get('class_name')
        sec = request.data.get('section')
        sub_name = request.data.get('subject')
        
        # 1. Validate Inputs
        if not all([cls, sec, sub_name]):
            return Response({"error": "class_name, section, and subject are required"}, 400)

        # 2. Strict Permission Check (With Year)
        if not check_permission(user, cls, sec, sub_name, active_year):
            return Response({"error": f"You are not assigned to teach {sub_name} for {cls}-{sec} in {active_year.name}"}, 403)

        # 3. Get Subject Object (Needed for ForeignKey)
        try:
            subject_obj = Subject.objects.get(name__iexact=sub_name, standard__name=cls)
        except Subject.DoesNotExist:
            return Response({"error": "Subject not found"}, 404)

        # 4. Save (Inject Year)
        serializer = SubjectMaterialSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(
                teacher=user, 
                subject=subject_obj, 
                academic_year=active_year
            )
            return Response({"message": "Material posted successfully", "data": serializer.data}, 201)
        
        return Response(serializer.errors, 400)

    # B. GET: View Materials (Filtered)
    def get(self, request):
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "No Active Year"}, 500)

        cls = request.query_params.get('class_name')
        sec = request.query_params.get('section')
        sub = request.query_params.get('subject')
        date = request.query_params.get('date')

        if not all([cls, sec, sub]):
             return Response({"error": "Params 'class_name', 'section', 'subject' are required."}, 400)

        # Permission check for Teachers viewing list
        if not check_permission(request.user, cls, sec, sub, active_year):
             return Response({"error": "Access Denied. You do not teach this subject/class."}, 403)

        materials = SubjectMaterial.objects.filter(
            academic_year=active_year, # <--- Strict Filter
            class_name=cls,
            section=sec,
            subject__name__iexact=sub
        )

        if date:
            materials = materials.filter(created_at__date=date)

        serializer = SubjectMaterialSerializer(materials.order_by('-created_at'), many=True)
        return Response({"data": serializer.data, "year": active_year.name}, 200)

    # C. PUT: Edit Description Only
    def put(self, request):
        mat_id = request.data.get('material_id')
        new_desc = request.data.get('description')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        # 1. Find Material owned by this teacher & Active Year
        material = get_object_or_404(
            SubjectMaterial, 
            id=mat_id, 
            teacher=request.user, 
            academic_year=active_year
        )
        
        # 2. Verify Teacher STILL has permission
        if not check_permission(request.user, material.class_name, material.section, material.subject.name, active_year):
             return Response({"error": "Permission revoked. You no longer teach this class."}, 403)

        if new_desc:
            material.description = new_desc
            material.save()
            return Response({"message": "Description updated."})
        return Response({"error": "Description required"}, 400)

    # D. DELETE: Whole Material
    def delete(self, request):
        mat_id = request.query_params.get('material_id')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        material = get_object_or_404(
            SubjectMaterial, 
            id=mat_id, 
            teacher=request.user,
            academic_year=active_year # <--- Strict Filter
        )
        
        if not check_permission(request.user, material.class_name, material.section, material.subject.name, active_year):
             return Response({"error": "Permission revoked."}, 403)

        # Delete File from storage
        if material.file and os.path.isfile(material.file.path):
            os.remove(material.file.path)
            
        material.delete()
        return Response({"message": "Material deleted successfully."}, 200)


# ==========================================
# 2. FILE MANAGER (DELETE/REPOST FILE)
# ==========================================
class SubjectMaterialFileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def delete(self, request):
        mat_id = request.query_params.get('material_id')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        material = get_object_or_404(
            SubjectMaterial, 
            id=mat_id, 
            teacher=request.user,
            academic_year=active_year
        )

        if material.file:
            if os.path.isfile(material.file.path):
                os.remove(material.file.path)
            material.file = None
            material.save()
            return Response({"message": "File deleted. Description remains."})
        return Response({"error": "No file to delete"}, 400)

    def post(self, request):
        mat_id = request.data.get('material_id')
        new_file = request.FILES.get('file')
        
        try:
            active_year = AcademicYear.objects.get(is_current=True)
        except:
             return Response({"error": "Year Error"}, 500)

        material = get_object_or_404(
            SubjectMaterial, 
            id=mat_id, 
            teacher=request.user,
            academic_year=active_year
        )
        
        if not new_file: return Response({"error": "No file provided"}, 400)
        if material.file: return Response({"error": "File exists. Delete it first."}, 400)

        material.file = new_file
        material.save()
        return Response({"message": "File uploaded successfully."})


# ==========================================
# 3. STUDENT VIEW (ENROLLMENT UPDATED)
# ==========================================
class StudentSubjectMaterialView(APIView):
    """
    GET: View Subject Materials (ACTIVE YEAR ONLY).
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
            return Response({"error": "No Active Year"}, 500)

        # 2. Get Enrolled Section (Source of Truth)
        # REPLACES: if not student.section ...
        try:
            enrollment = Enrollment.objects.get(
                student=student, 
                academic_year=active_year, 
                is_active=True
            )
            my_section = enrollment.section
        except Enrollment.DoesNotExist:
            return Response({"error": "You are not enrolled in any active class."}, 400)

        # 3. Validate Subject Param
        req_subject = request.query_params.get('subject')
        if not req_subject:
            return Response({"error": "Please provide ?subject=Name"}, 400)

        # 4. Filter Materials (Active Year & Section Match)
        # Uses my_section from Enrollment, ensuring user sees materials for their current class
        materials = SubjectMaterial.objects.filter(
            academic_year=active_year, 
            class_name=my_section.standard.name,
            subject__name__iexact=req_subject
        ).filter(
            models.Q(section=my_section.name) | models.Q(section="All") 
        )

        # 5. Filter by Date (Optional)
        req_date = request.query_params.get('date')
        if req_date:
            materials = materials.filter(created_at__date=req_date)

        serializer = SubjectMaterialSerializer(materials.order_by('-created_at'), many=True)
        return Response({"data": serializer.data, "year": active_year.name}, 200)


class StudentMaterialsCombinedView(APIView):
    """
    GET: Combined Class Resources + Subject Materials for ACTIVE YEAR.
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
            return Response({"error": "No Active Year"}, 500)

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

        # 3. Class Resources (All subjects + null subjects) for this section & year
        class_resources = ClassResource.objects.filter(
            section=my_section,
            academic_year=active_year
        ).order_by('-created_at')

        # 4. Subject Materials (All subjects) for this class/section & year
        subject_materials = SubjectMaterial.objects.filter(
            academic_year=active_year,
            class_name=my_section.standard.name
        ).filter(
            models.Q(section=my_section.name) | models.Q(section="All")
        ).order_by('-created_at')

        class_serializer = ClassResourceSerializer(class_resources, many=True)
        subject_serializer = SubjectMaterialSerializer(subject_materials, many=True)

        return Response({
            "class_resources": class_serializer.data,
            "subject_materials": subject_serializer.data,
            "year": active_year.name
        }, 200)
