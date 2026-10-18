from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Q
from django.db import transaction

# CHANGE 1: Import your specific permission class here.
# If it's in a specific file like 'accounts.permissions', adjust the import below:
from schooladmin.permissions import IsAdmin, IsSuperAdmin
from django.shortcuts import get_object_or_404

from staff.permissions import IsOwnerAdminOrFinanceAdminStaffOrHostelWarden
from .models import AcademicYear, Institution, School
from .serializers import AcademicYearSerializer, InstitutionSerializer, SchoolSerializer
from .tenant import get_requested_school, scope_queryset_for_user


def _delete_school_data(school):
    """Remove protected historical references before cascading a school."""
    from hostel.models import HostelAllocation
    from promotions.models import PromotionBatch, PromotionRecord

    student_ids = list(school.students.values_list('id', flat=True))
    year_ids = list(school.academic_years.values_list('id', flat=True))
    standard_ids = list(school.standards.values_list('id', flat=True))
    section_ids = list(school.sections.values_list('id', flat=True))

    # Remove protected historical references before their target rows.
    PromotionRecord.objects.filter(
        Q(student_id__in=student_ids)
        | Q(from_enrollment__school=school)
        | Q(to_enrollment__school=school)
        | Q(from_standard_id__in=standard_ids)
        | Q(from_section_id__in=section_ids)
        | Q(target_standard_id__in=standard_ids)
        | Q(target_section_id__in=section_ids)
    ).delete()
    PromotionBatch.objects.filter(
        Q(from_academic_year_id__in=year_ids)
        | Q(to_academic_year_id__in=year_ids)
    ).delete()
    HostelAllocation.objects.filter(
        Q(student_id__in=student_ids)
        | Q(academic_year_id__in=year_ids)
    ).delete()

    # Students protect their standards and sections, so remove them before
    # deleting those school structures. Their linked accounts cascade with them.
    school.students.all().delete()


class InstitutionListCreateView(generics.ListCreateAPIView):
    serializer_class = InstitutionSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get_queryset(self):
        return Institution.objects.annotate(school_count=Count('schools')).order_by('name')


class InstitutionDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = InstitutionSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get_queryset(self):
        return Institution.objects.annotate(school_count=Count('schools'))

    def perform_destroy(self, instance):
        with transaction.atomic():
            for school in instance.schools.all():
                _delete_school_data(school)
            instance.delete()


class SchoolListCreateView(generics.ListCreateAPIView):
    serializer_class = SchoolSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        queryset = School.objects.select_related('institution').annotate(
            student_count=Count('students', distinct=True),
            teacher_count=Count('teachers', distinct=True),
            staff_count=Count('staff_members', distinct=True),
            admin_count=Count('adminprofile', distinct=True),
        )
        return scope_queryset_for_user(queryset, self.request, field='id').order_by('name')


class SchoolDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SchoolSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        queryset = School.objects.select_related('institution').annotate(
            student_count=Count('students', distinct=True),
            teacher_count=Count('teachers', distinct=True),
            staff_count=Count('staff_members', distinct=True),
            admin_count=Count('adminprofile', distinct=True),
        )
        return scope_queryset_for_user(queryset, self.request, field='id')

    def perform_destroy(self, instance):
        with transaction.atomic():
            _delete_school_data(instance)
            instance.delete()

class AcademicYearListCreateView(APIView):
    # CHANGE 2: Use your specific class 'IsAdmin'
    permission_classes = [IsAuthenticated, IsAdmin] 

    def get_permissions(self):
        # Allow admin + finance/admin staff to READ academic years.
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsOwnerAdminOrFinanceAdminStaffOrHostelWarden()]
        # Keep CREATE restricted to admin.
        return [IsAuthenticated(), IsAdmin()]

    def get(self, request):
        requested_school = get_requested_school(request)
        years = AcademicYear.objects.all()
        if requested_school:
            years = years.filter(Q(school=requested_school) | Q(school__isnull=True))
        else:
            years = scope_queryset_for_user(years, request)
        serializer = AcademicYearSerializer(years, many=True)
        return Response(serializer.data)

    def post(self, request):
        school = get_requested_school(request)
        if not school:
            return Response({"school_id": "Select a school before creating academic years."}, status=400)
        serializer = AcademicYearSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(school=school)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class AcademicYearDetailView(APIView):
    # CHANGE 3: Use 'IsAdmin' here too
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_permissions(self):
        # Allow admin + finance/admin staff to READ one academic year.
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsOwnerAdminOrFinanceAdminStaffOrHostelWarden()]
        # Keep UPDATE/DELETE restricted to admin.
        return [IsAuthenticated(), IsAdmin()]

    def get_object(self, pk):
        queryset = scope_queryset_for_user(AcademicYear.objects.all(), self.request)
        return get_object_or_404(queryset, pk=pk)

    def get(self, request, pk):
        year = self.get_object(pk)
        serializer = AcademicYearSerializer(year)
        return Response(serializer.data)

    def put(self, request, pk):
        year = self.get_object(pk)
        serializer = AcademicYearSerializer(year, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        year = self.get_object(pk)
        year.delete()
        return Response({"message": "Academic Year deleted successfully"}, status=status.HTTP_204_NO_CONTENT)
