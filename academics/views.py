from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction

from django.shortcuts import get_object_or_404
from staff.permissions import IsOwnerAdminOrFinanceAdminStaff, IsOwnerAdminOrAdminStaff
from subjects.models import Subject  # <--- IMPORT SUBJECT MODEL HERE
from school.tenant import get_requested_school, scope_queryset_for_user

from .models import Standard, Section
from .serializers import (
    StandardSerializer, 
    SectionSerializer, 
    SectionSetupSerializer,
    SimpleStandardSerializer
)

# --- VIEW 1: GET ALL CLASSES ---
class StandardListCreateView(generics.ListCreateAPIView):
    serializer_class = SimpleStandardSerializer      # <--- Use the Simple one here
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get_queryset(self):
        return scope_queryset_for_user(Standard.objects.all(), self.request).order_by('id')

    def perform_create(self, serializer):
        serializer.save(school=get_requested_school(self.request))

# ==========================================
# VIEW 2: GET SECTIONS (Updated for Name Filter)
# ==========================================
class SectionListCreateView(generics.ListCreateAPIView):
    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get_queryset(self):
        queryset = scope_queryset_for_user(Section.objects.all(), self.request)
        
        # Filter by ID (Legacy support)
        standard_id = self.request.query_params.get('standard_id')
        if standard_id:
            queryset = queryset.filter(standard__id=standard_id)
            
        # NEW: Filter by Standard Name (e.g., ?standard_name=10)
        standard_name = self.request.query_params.get('standard_name')
        if standard_name:
            queryset = queryset.filter(standard__name=standard_name)
            
        return queryset

# --- VIEW 3: SETUP SINGLE SECTION (Legacy) ---
class SetupSectionView(APIView):
    # SECURE: Only Admin
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff] 

    def post(self, request):
        serializer = SectionSetupSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Created", "data": serializer.data}, status=201)
        return Response(serializer.errors, status=400)


# --- VIEW 4: BULK CREATE STANDARDS (Screen 1) ---
class BulkCreateStandardsView(APIView):
    """
    Creates multiple classes at once (e.g., 1 to 12).
    """
    # SECURE: Only Admin
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff] 

    def post(self, request):
        standards_list = request.data.get('standards', [])
        
        if not standards_list or not isinstance(standards_list, list):
            return Response({"error": "Please provide a list of standard names."}, status=400)

        created_count = 0
        school = get_requested_school(request)
        if not school:
            return Response({"error": "Select a school before creating classes."}, status=400)
        for name in standards_list:
            _, created = Standard.objects.get_or_create(
                school=school,
                name=str(name),
            )
            if created:
                created_count += 1

        return Response({
            "message": "Standards processed successfully.",
            "created_count": created_count,
            "total_standards": len(standards_list)
        }, status=status.HTTP_201_CREATED)


# --- VIEW 5: BULK ASSIGN SECTIONS (Screen 2 - One Shot) ---
class BulkAssignSectionsView(APIView):
    """
    Assigns sections to MULTIPLE classes in a single request.
    """
    # SECURE: Only Admin
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        data_list = request.data
        
        if not isinstance(data_list, list):
            return Response({"error": "Expected a list of class mappings."}, status=400)

        created_log = []
        total_created = 0
        school = get_requested_school(request)
        if not school:
            return Response({"error": "Select a school before creating sections."}, status=400)

        try:
            with transaction.atomic():
                for item in data_list:
                    class_name = item.get('class_name')
                    section_names = item.get('sections', [])

                    if not class_name:
                        continue 

                    standard_obj, _ = Standard.objects.get_or_create(
                        school=school,
                        name=str(class_name),
                    )
                    
                    class_created_count = 0
                    for sec_name in section_names:
                        _, created = Section.objects.get_or_create(
                            standard=standard_obj,
                            name=str(sec_name),
                            defaults={"school": school},
                        )
                        if created:
                            class_created_count += 1
                            total_created += 1
                    
                    created_log.append({
                        "class": class_name,
                        "new_sections": class_created_count
                    })

            return Response({
                "message": "Bulk assignment completed successfully.",
                "total_new_sections": total_created,
                "details": created_log
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"error": f"Transaction Failed: {str(e)}"}, status=500)


# ==========================================
# 6. MASTER STRUCTURE VIEW (GET & DELETE)
# ==========================================
class SchoolStructureView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    # --- A. MASTER VIEW (Get Hierarchy) ---
    def get(self, request):
        structure_data = []
        
        # 1. Fetch all classes (Standards)
        standards = scope_queryset_for_user(Standard.objects.all(), request).order_by('id')

        for std in standards:
            # 2. Get Sections for this class
            sections = std.sections.all().values_list('name', flat=True)
            
            # 3. Get Subjects for this class
            subjects = Subject.objects.filter(standard=std).values_list('name', flat=True)

            structure_data.append({
                "class": std.name,
                "sections": list(sections), # ['A', 'B']
                "subjects": list(subjects)  # ['Maths', 'Physics']
            })

        return Response({
            "status": 200,
            "count": len(structure_data),
            "data": structure_data
        })

    # --- B. SMART DELETE (Class / Section / Subject) ---
    def delete(self, request):
        class_name = request.query_params.get('class')
        section_name = request.query_params.get('section')
        subject_name = request.query_params.get('subject')

        if not class_name:
            return Response({"error": "Param 'class' is required for deletion."}, 400)

        try:
            standard = scope_queryset_for_user(Standard.objects.all(), request).get(name=class_name)
        except Standard.DoesNotExist:
            return Response({"error": "Class not found."}, 404)

        # Scenario 1: Delete Specific SECTION
        if section_name:
            deleted_count, _ = Section.objects.filter(standard=standard, name=section_name).delete()
            if deleted_count == 0:
                return Response({"error": f"Section {section_name} not found in Class {class_name}"}, 404)
            return Response({"message": f"Section {section_name} deleted from Class {class_name}."})

        # Scenario 2: Delete Specific SUBJECT
        elif subject_name:
            deleted_count, _ = Subject.objects.filter(standard=standard, name=subject_name).delete()
            if deleted_count == 0:
                return Response({"error": f"Subject {subject_name} not found in Class {class_name}"}, 404)
            return Response({"message": f"Subject {subject_name} deleted from Class {class_name}."})

        # Scenario 3: Delete WHOLE CLASS (Standard)
        else:
            standard.delete()
            # Django's CASCADE will automatically delete all linked Sections & Subjects
            return Response({"message": f"Class {class_name} and all its contents deleted successfully."})


# ==========================================
# 7. STRUCTURE EDIT VIEW (PUT - Rename)
# ==========================================
class StructureEditView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def put(self, request):
        edit_type = request.data.get('type') # 'section' or 'subject'
        class_name = request.data.get('class_name')
        old_name = request.data.get('old_name')
        new_name = request.data.get('new_name')

        if not all([edit_type, class_name, old_name, new_name]):
            return Response({"error": "Missing fields. Required: type, class_name, old_name, new_name"}, 400)

        # 1. Find the Class
        try:
            standard = scope_queryset_for_user(Standard.objects.all(), request).get(name=class_name)
        except Standard.DoesNotExist:
            return Response({"error": "Class not found"}, 404)

        # --- A. RENAME SECTION ---
        if edit_type == 'section':
            try:
                section = Section.objects.get(standard=standard, name=old_name)
                section.name = new_name
                section.save()
                return Response({"message": f"Section renamed from {old_name} to {new_name}"})
            except Section.DoesNotExist:
                return Response({"error": "Section not found"}, 404)
            except Exception as e:
                return Response({"error": f"Update failed (Duplicate name?): {str(e)}"}, 400)

        # --- B. RENAME SUBJECT ---
        elif edit_type == 'subject':
            try:
                subject = Subject.objects.get(standard=standard, name=old_name)
                subject.name = new_name
                subject.save()
                return Response({"message": f"Subject renamed from {old_name} to {new_name}"})
            except Subject.DoesNotExist:
                return Response({"error": "Subject not found"}, 404)
            except Exception as e:
                return Response({"error": f"Update failed: {str(e)}"}, 400)

        else:
            return Response({"error": "Invalid type. Use 'section' or 'subject'."}, 400)
        
class StandardListCreateViewNew(generics.ListCreateAPIView):
    serializer_class = StandardSerializer      # <--- Use the Simple one here
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get_queryset(self):
        return scope_queryset_for_user(Standard.objects.all(), self.request).order_by('id')

    def perform_create(self, serializer):
        serializer.save(school=get_requested_school(self.request))

    def get_permissions(self):
        # Allow admin + finance/admin staff to list classes.
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsOwnerAdminOrFinanceAdminStaff()]
        # Allow CREATE for owner admin + admin_staff.
        return [IsAuthenticated(), IsOwnerAdminOrAdminStaff()]
